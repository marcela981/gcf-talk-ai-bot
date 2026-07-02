"""Adapter de Deck (REST) sobre Nextcloud — implementa `DeckPort` (lectura + crear card).

Mismo patrón que el adapter de Calendar (ADR-016, Bloque 2.2): encapsula su **propio**
cliente HTTP firmado que replica la auth de AppAPI
(``AUTHORIZATION-APP-API: base64(uid:app_secret)``) para **impersonar** al usuario, SIN
tocar el adaptador privado ``nc._session.adapter`` (deuda **D-IMP-1** de ADR-016) ni
importar nada de ``app/_spike``. El ``app_secret`` **NUNCA** se loguea.

API Deck v1.0 (verificada contra la doc oficial ``nextcloud/deck`` ``docs/API.md``, no
inventada):
  * ``GET  /index.php/apps/deck/api/v1.0/boards``                              (list)
  * ``GET  /index.php/apps/deck/api/v1.0/boards/{boardId}/stacks``             (stacks+cards)
  * ``POST /index.php/apps/deck/api/v1.0/boards/{boardId}/stacks/{stackId}/cards`` (crear)
Todas llevan ``OCS-APIRequest: true`` (validado en el spike para ``GET /boards``); la
escritura además ``Content-Type: application/json``. El body de creación exige
``title``/``type``/``order`` (opcionales ``description``/``duedate`` ISO-8601).

Status HTTP crudo como **dato**: lecturas no-200 ⇒ `DeckError` (la skill lo traduce a
fallo); creación 200/201 ⇒ `CreatedCard` ok, y 400/403/404 (o tablero/columna inexistente)
⇒ `CreatedCard(ok=False, error=...)`, sin excepción cruda. El parseo JSON→dominio se
delega a ``domain.deck`` (puro, testeable). ``transport`` es inyectable para tests sin red.

NOTA: la duplicación del builder de cabeceras firmadas con el adapter de Calendar es
deliberada (cada adapter encapsula su propio cliente, D-IMP-1); NO se refactoriza aquí
para no tocar Calendar. La **asignación de usuarios** a la tarjeta queda para 2.3b.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import Any

import httpx

from app.domain.deck import (
    Board,
    BoardStatus,
    CreatedCard,
    filter_stacks_by_assignee,
    find_board,
    find_stack,
    parse_boards,
    parse_created_card_id,
    parse_stacks,
)

logger = logging.getLogger(__name__)

_DECK_API_BASE = "/index.php/apps/deck/api/v1.0"
_JSON_CONTENT_TYPE = "application/json"
_CREATED_OK = frozenset({200, 201})


class DeckError(Exception):
    """Fallo del adapter de Deck (transporte, HTTP de lectura, respuesta inesperada)."""


class DeckRestAdapter:
    """Implementa `DeckPort` contra la REST de Deck, impersonando al usuario."""

    def __init__(
        self,
        *,
        endpoint: str,
        app_id: str,
        app_version: str,
        app_secret: str,
        aa_version: str = "2.2.0",
        api_base: str = _DECK_API_BASE,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not endpoint or not app_id or not app_secret:
            raise DeckError(
                "NEXTCLOUD_URL, APP_ID y APP_SECRET son obligatorios para el "
                "adapter de Deck impersonado."
            )
        # Normaliza igual que nc_py_api / el adapter de Calendar: raíz NC sin /index.php.
        self._endpoint = endpoint.removesuffix("/index.php").rstrip("/")
        self._api_base = "/" + api_base.strip("/")
        self._app_id = app_id
        self._app_version = app_version
        self._app_secret = app_secret  # NUNCA se loguea
        self._aa_version = aa_version
        self._timeout_s = timeout_s
        self._transport = transport

    async def list_boards(self, uid: str) -> list[Board]:
        if not uid:
            raise DeckError("uid vacío: no hay identidad que impersonar.")
        payload = await self._get_json(uid, "/boards")
        boards = parse_boards(payload)
        logger.info("Tableros de Deck para %s: %d.", uid, len(boards))
        return boards

    async def get_board_status(
        self, uid: str, board: str | int, assigned_to_uid: str | None = None
    ) -> BoardStatus:
        if not uid:
            raise DeckError("uid vacío: no hay identidad que impersonar.")
        match = find_board(await self.list_boards(uid), board)
        if match is None:
            raise DeckError(f"No encontré un tablero que coincida con {board!r}.")
        stacks_payload = await self._get_json(uid, f"/boards/{match.id}/stacks")
        stacks = tuple(parse_stacks(stacks_payload))
        if assigned_to_uid:
            # "Solo mías": conserva solo las tarjetas asignadas a ese uid (2.3).
            stacks = filter_stacks_by_assignee(stacks, assigned_to_uid)
        return BoardStatus(board=match, stacks=stacks)

    async def create_card(
        self,
        uid: str,
        board: str | int,
        stack: str | int,
        title: str,
        *,
        description: str | None = None,
        duedate: datetime | None = None,
    ) -> CreatedCard:
        if not uid:
            raise DeckError("uid vacío: no hay identidad que impersonar.")

        # Resolución board→id y stack→id (por id o título). Los fallos de negocio
        # (inexistente) y los HTTP de lectura se devuelven como CreatedCard (dato).
        try:
            board_match = find_board(await self.list_boards(uid), board)
            if board_match is None:
                return CreatedCard(
                    ok=False,
                    status=404,
                    error=f"No encontré un tablero que coincida con {board!r}.",
                )
            stacks = parse_stacks(
                await self._get_json(uid, f"/boards/{board_match.id}/stacks")
            )
            stack_match = find_stack(stacks, stack)
            if stack_match is None:
                return CreatedCard(
                    ok=False,
                    status=404,
                    error=(
                        f"No encontré la columna {stack!r} en el tablero "
                        f"{board_match.title!r}."
                    ),
                )
        except DeckError as exc:
            return CreatedCard(
                ok=False,
                status=0,
                error=f"No se pudo preparar la creación de la tarjeta: {exc}",
            )

        # 'order' coloca la tarjeta al final de la columna (nº de tarjetas actuales).
        body: dict[str, Any] = {
            "title": title,
            "type": "plain",
            "order": len(stack_match.cards),
        }
        if description:
            body["description"] = description
        if duedate is not None:
            body["duedate"] = duedate.isoformat()  # ISO-8601 con offset (Deck lo acepta)

        path = f"{self._api_base}/boards/{board_match.id}/stacks/{stack_match.id}/cards"
        async with self._client() as client:
            resp = await client.post(
                path,
                headers={**self._headers(uid), "Content-Type": _JSON_CONTENT_TYPE},
                json=body,
            )

        if resp.status_code in _CREATED_OK:
            card_id = parse_created_card_id(_safe_json(resp))
            url = self._card_url(board_match.id, card_id) if card_id else None
            logger.info(
                "Tarjeta creada para %s en board=%s stack=%s (HTTP %s).",
                uid,
                board_match.id,
                stack_match.id,
                resp.status_code,
            )
            return CreatedCard(
                ok=True, status=resp.status_code, card_id=card_id, url=url
            )

        logger.warning(
            "POST de tarjeta devolvió HTTP %s para uid=%s (board=%s stack=%s).",
            resp.status_code,
            uid,
            board_match.id,
            stack_match.id,
        )
        return CreatedCard(
            ok=False,
            status=resp.status_code,
            error=_card_error_message(
                resp.status_code, board_match.title, stack_match.title
            ),
        )

    # --- infraestructura interna --------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._endpoint,
            timeout=self._timeout_s,
            transport=self._transport,
        )

    async def _get_json(self, uid: str, path: str) -> Any:
        """GET a la API de Deck; no-200 ⇒ `DeckError`, cuerpo no-JSON ⇒ `DeckError`."""
        async with self._client() as client:
            resp = await client.get(
                f"{self._api_base}{path}", headers=self._headers(uid)
            )
        if resp.status_code != 200:
            raise DeckError(
                f"GET {path} devolvió HTTP {resp.status_code} (uid={uid!r})."
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise DeckError(f"Respuesta no-JSON de Deck en {path}: {exc}") from exc

    def _headers(self, uid: str) -> dict[str, str]:
        """Cabeceras AppAPI firmadas que impersonan a ``uid``. El secreto no se loguea."""
        token = base64.b64encode(
            f"{uid}:{self._app_secret}".encode("utf-8")
        ).decode("ascii")
        return {
            "AA-VERSION": self._aa_version,
            "EX-APP-ID": self._app_id,
            "EX-APP-VERSION": self._app_version,
            "OCS-APIRequest": "true",
            "AUTHORIZATION-APP-API": token,
            "User-Agent": f"ExApp/{self._app_id}/{self._app_version}",
        }

    def _card_url(self, board_id: int, card_id: int) -> str:
        """Deep-link **best-effort** a la tarjeta en la UI de Deck.

        INFERENCIA: la API no devuelve una URL; este enlace se **construye** con el
        patrón de rutas de la UI de Deck y puede variar entre versiones. El identificador
        fiable es ``card_id`` (viene en la respuesta del POST).
        """
        return f"{self._endpoint}/index.php/apps/deck/#/board/{board_id}/card/{card_id}"


def _safe_json(resp: httpx.Response) -> Any:
    """``resp.json()`` tolerante: cuerpo vacío/no-JSON ⇒ ``None`` (no rompe el éxito)."""
    try:
        return resp.json()
    except ValueError:
        return None


def _card_error_message(status: int, board: str, stack: str) -> str:
    """Mensaje claro para los rechazos esperables del POST de creación (Bloque 2.3)."""
    if status == 403:
        return (
            f"No se pudo crear la tarjeta (HTTP 403): posible falta de permiso de "
            f"escritura en el tablero {board!r}, o rechazo por CSRF."
        )
    if status == 404:
        return (
            f"No se pudo crear la tarjeta (HTTP 404): el tablero {board!r} o la columna "
            f"{stack!r} no se encontró al crear."
        )
    if status == 400:
        return (
            "No se pudo crear la tarjeta (HTTP 400): datos inválidos (revisa el título "
            "o la fecha límite)."
        )
    return f"No se pudo crear la tarjeta (HTTP {status})."

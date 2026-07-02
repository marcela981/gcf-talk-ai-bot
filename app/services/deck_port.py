"""Contrato `DeckPort` (ADR-016/ADR-018, Bloque 2.3): Deck impersonado (lectura + crear card).

Mismo patrón que `CalendarPort`: las skills dependen de esta interfaz, no del adapter REST
concreto; el adapter de Nextcloud (``adapters/nextcloud_deck_adapter.py``) la implementa.
Sin dependencias de framework (regla de capas, ARCHITECTURE §3): el contrato vive en
``services`` y habla en value objects de dominio (``app.domain.deck``).

Alcance 2.3: listar tableros, ver el estado de un tablero (columnas + tarjetas) y **crear**
una tarjeta. La **asignación de usuarios** a la tarjeta queda para 2.3b (requiere resolver
nombre→uid), por eso ``create_card`` no la expone aún.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain.deck import Board, BoardStatus, CreatedCard


@runtime_checkable
class DeckPort(Protocol):
    """Acceso a Deck de un usuario, bajo SU identidad (lectura + creación de tarjetas)."""

    async def list_boards(self, uid: str) -> list[Board]:
        """Tableros del usuario ``uid`` (``id``, ``title``), **impersonando** a ``uid``.

        Puede lanzar un error propio del adapter ante fallo de transporte/HTTP; el
        llamador (la skill) lo traduce a ``SkillResult.failure`` (ADR-017/018).
        """
        ...

    async def get_board_status(self, uid: str, board: str | int) -> BoardStatus:
        """Estado de ``board`` (columnas + tarjetas), **impersonando** a ``uid``.

        ``board`` puede ser el **id** numérico o el **título** (se resuelve por título
        case-insensitive contra :meth:`list_boards` si no es numérico). Lanza si el
        tablero no existe o ante fallo HTTP/transporte (la skill lo traduce a fallo).
        """
        ...

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
        """Crea una tarjeta ``title`` en ``board``/``stack``, **impersonando** a ``uid``.

        Resuelve ``board``→id y ``stack``→id (por id o título) vía GET previo y hace
        ``POST /boards/{boardId}/stacks/{stackId}/cards``. Devuelve un :class:`CreatedCard`:
        ``ok=True`` con ``card_id`` cuando Deck confirma (200/201); ``ok=False`` con un
        ``error`` legible ante 400/403/404, tablero/columna inexistente u otros rechazos
        esperables — el fallo es **dato, no excepción cruda**. Un fallo de transporte sí
        puede propagarse como excepción del adapter.

        La **asignación de usuarios** NO se soporta aquí (Bloque 2.3b: requiere nombre→uid).
        """
        ...

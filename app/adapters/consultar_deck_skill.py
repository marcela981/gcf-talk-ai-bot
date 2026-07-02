"""Skill de Deck (read-only): `consultar_deck` (ADR-016/ADR-018, Bloque 2.3).

Como las skills de Calendar, **usa la identidad**: requiere ``actor.impersonated_uid``; si
es ``None`` (invitado/federado, ADR-016) se **rehúsa**, SIN tocar Deck. El I/O vive en el
`DeckPort` inyectado; ``execute`` queda delgado (ADR-018).

Comportamiento:
  * sin ``tablero`` → devuelve la lista de tableros (para que el usuario elija).
  * con ``tablero`` → devuelve sus columnas y tarjetas; si además viene ``columna``, filtra
    a esa columna (case-insensitive).

READ-ONLY: no crea ni modifica (para crear usa la skill `crear_tarjeta_deck`).
"""
from __future__ import annotations

import logging
from typing import Any

from app.domain.actor_context import ActorContext
from app.domain.deck import BoardStatus, Card
from app.domain.skill_result import SkillResult
from app.services.deck_port import DeckPort

logger = logging.getLogger(__name__)

_NAME = "consultar_deck"
_DESC_MAX = 200
_DESCRIPTION = (
    "Consulta los tableros y tarjetas (tareas) de Deck del usuario que te escribe. Úsala "
    "cuando pregunte por sus tareas, tableros o el estado de un proyecto, p. ej.: "
    "'¿qué tareas tengo?', '¿qué tableros tengo?', 'estado del board TECH PROY', "
    "'¿qué hay en la columna To Do de TECH PROY?', '¿qué tengo pendiente en Ventas?'. "
    "Pasa 'tablero' (id o título) para ver sus columnas y tarjetas; añade 'columna' para "
    "filtrar a una sola columna. Si NO sabes qué tablero, OMITE 'tablero' y la tool "
    "devuelve la lista de tableros para elegir. Devuelve título, fecha límite y una "
    "descripción breve de cada tarjeta. SOLO lectura: no crea ni modifica tarjetas."
)
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tablero": {
            "type": "string",
            "description": (
                "Tablero a consultar: id numérico o título (p. ej. 'TECH PROY'). "
                "Omítelo para obtener la lista de tableros."
            ),
        },
        "columna": {
            "type": "string",
            "description": (
                "Filtra a una columna por título (p. ej. 'To Do'), case-insensitive. "
                "Requiere 'tablero'. Omítela para ver todas las columnas."
            ),
        },
    },
    "additionalProperties": False,
}

_NO_IDENTITY_MSG = (
    "Acción no disponible para invitados o usuarios sin identidad local: solo "
    "puedo consultar el Deck de usuarios de Nextcloud."
)


class ConsultarDeckSkill:
    """Implementa el contrato `Skill` delegando la lectura en un `DeckPort`."""

    def __init__(self, *, deck: DeckPort) -> None:
        self._deck = deck

    @property
    def name(self) -> str:
        return _NAME

    @property
    def description(self) -> str:
        return _DESCRIPTION

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return _PARAMETERS_SCHEMA

    async def execute(self, args: dict[str, Any], actor: ActorContext) -> SkillResult:
        """Rehúsa sin identidad; si la hay, lista tableros o el estado de uno via `DeckPort`."""
        if actor.impersonated_uid is None:
            return SkillResult.failure(_NO_IDENTITY_MSG)

        board_ref = args.get("tablero")
        has_board = board_ref is not None and str(board_ref).strip()

        try:
            if not has_board:
                boards = await self._deck.list_boards(actor.impersonated_uid)
                return SkillResult.success(
                    {
                        "tableros": [{"id": b.id, "titulo": b.title} for b in boards],
                        "total": len(boards),
                    }
                )
            status = await self._deck.get_board_status(
                actor.impersonated_uid, str(board_ref).strip()
            )
        except Exception as exc:  # noqa: BLE001 — devolver el fallo como dato (ADR-018)
            logger.exception("Consulta de Deck falló para %s.", actor.impersonated_uid)
            return SkillResult.failure(f"Error consultando Deck: {exc}")

        return SkillResult.success(_board_status_to_dict(status, args.get("columna")))


def _board_status_to_dict(status: BoardStatus, columna: Any) -> dict[str, Any]:
    """Serializa el tablero; si ``columna`` viene, filtra a esa columna (case-insensitive)."""
    wanted = str(columna).strip().casefold() if columna and str(columna).strip() else None
    columnas = []
    total = 0
    for stack in status.stacks:
        if wanted is not None and stack.title.casefold() != wanted:
            continue
        tarjetas = [_card_to_dict(card) for card in stack.cards]
        total += len(tarjetas)
        columnas.append({"columna": stack.title, "tarjetas": tarjetas})
    return {
        "tablero": {"id": status.board.id, "titulo": status.board.title},
        "columnas": columnas,
        "total_tarjetas": total,
    }


def _card_to_dict(card: Card) -> dict[str, Any]:
    """Tarjeta con descripción **breve** (recortada) para no inflar el contexto del LLM."""
    description = card.description
    if description and len(description) > _DESC_MAX:
        description = description[:_DESC_MAX].rstrip() + "…"
    return {
        "titulo": card.title,
        "vence": card.due_date,
        "descripcion": description,
    }

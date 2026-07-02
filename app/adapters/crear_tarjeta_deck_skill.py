"""Skill de Deck (escritura): `crear_tarjeta_deck` (ADR-016/ADR-018, Bloque 2.3).

Skill NUEVA y separada de la de lectura (SRP, mismo criterio que Calendar 2.2: crear y
consultar son intenciones distintas ante el LLM). **Usa la identidad**: requiere
``actor.impersonated_uid``; si es ``None`` se **rehúsa**, SIN tocar Deck. El I/O vive en el
`DeckPort` inyectado; ``execute`` valida los args y delega.

La ASIGNACIÓN de usuarios a la tarjeta NO se soporta aquí — es el Bloque **2.3b** (requiere
resolver nombre→uid). El primer ``create_card`` real es la validación de escritura
impersonada de Deck (SPIKE_IMPERSONATION §6): comparte mecanismo con la escritura de
Calendar (2.2), así que si Calendar write pasa el smoke, esta hereda esa confianza.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone, tzinfo
from typing import Any

from app.domain.actor_context import ActorContext
from app.domain.skill_result import SkillResult
from app.services.deck_port import DeckPort

logger = logging.getLogger(__name__)

_NAME = "crear_tarjeta_deck"
_DESCRIPTION = (
    "Crea una tarjeta (tarea) en una columna de un tablero de Deck del usuario que te "
    "escribe. Úsala cuando pida crear, añadir o anotar una tarea/tarjeta, p. ej.: "
    "'añade una tarea \"llamar al cliente\" en la columna To Do de TECH PROY', "
    "'crea una tarjeta en Ventas para el viernes'. Requiere 'titulo', 'board' (id o "
    "título) y 'columna' (título de la columna). SOLO crea la tarjeta; NO asigna "
    "responsables (aún no soportado). Para consultar el tablero usa 'consultar_deck'.\n"
    "FECHA LÍMITE: si el usuario da un plazo, calcula 'fecha_limite' (ISO 'YYYY-MM-DD') a "
    "partir de la 'Fecha y hora actuales' del contexto, nunca de tu conocimiento previo; "
    "omítela si no hay plazo."
)
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "titulo": {
            "type": "string",
            "description": "Título de la tarjeta/tarea. Obligatorio.",
        },
        "board": {
            "type": "string",
            "description": "Tablero destino: id numérico o título (p. ej. 'TECH PROY').",
        },
        "columna": {
            "type": "string",
            "description": "Columna destino por título (p. ej. 'To Do').",
        },
        "descripcion": {
            "type": "string",
            "description": "Descripción/notas de la tarjeta (opcional, admite markdown).",
        },
        "fecha_limite": {
            "type": "string",
            "description": (
                "Fecha límite en ISO 'YYYY-MM-DD' (opcional). Calcúlala desde la 'Fecha y "
                "hora actuales' del contexto. Se interpreta en la zona del usuario."
            ),
        },
    },
    "required": ["titulo", "board", "columna"],
    "additionalProperties": False,
}

_NO_IDENTITY_MSG = (
    "Acción no disponible para invitados o usuarios sin identidad local: solo "
    "puedo crear tarjetas en el Deck de usuarios de Nextcloud."
)


class CrearTarjetaDeckSkill:
    """Implementa el contrato `Skill` delegando la creación en un `DeckPort`.

    ``tz`` es la zona del usuario: una 'fecha_limite' sin hora se ancla a **mediodía en esa
    zona** (una fecha, no un instante concreto); Deck guarda el instante y lo muestra en la
    zona del visor, así que el día se ve correcto para el usuario.
    """

    def __init__(self, *, deck: DeckPort, tz: tzinfo = timezone.utc) -> None:
        self._deck = deck
        self._tz = tz

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
        """Rehúsa sin identidad; si la hay, valida los args y crea la tarjeta via `DeckPort`."""
        if actor.impersonated_uid is None:
            return SkillResult.failure(_NO_IDENTITY_MSG)

        titulo = str(args.get("titulo") or "").strip()
        if not titulo:
            return SkillResult.failure("Falta el 'titulo' de la tarjeta.")
        board = str(args.get("board") or "").strip()
        if not board:
            return SkillResult.failure("Falta el 'board' (tablero) destino.")
        columna = str(args.get("columna") or "").strip()
        if not columna:
            return SkillResult.failure("Falta la 'columna' destino.")

        duedate, error = self._resolve_duedate(args.get("fecha_limite"))
        if error is not None:
            return SkillResult.failure(error)

        try:
            result = await self._deck.create_card(
                actor.impersonated_uid,
                board,
                columna,
                titulo,
                description=_optional(args.get("descripcion")),
                duedate=duedate,
            )
        except Exception as exc:  # noqa: BLE001 — devolver el fallo como dato (ADR-018)
            logger.exception(
                "Creación de tarjeta Deck falló para %s (%s).",
                actor.impersonated_uid,
                titulo,
            )
            return SkillResult.failure(f"Error creando la tarjeta: {exc}")

        if not result.ok:
            return SkillResult.failure(result.error or "No se pudo crear la tarjeta.")

        return SkillResult.success(
            {
                "creada": True,
                "titulo": titulo,
                "tablero": board,
                "columna": columna,
                "id": result.card_id,
                "url": result.url,
                "vence": duedate.isoformat() if duedate is not None else None,
            }
        )

    def _resolve_duedate(
        self, raw: Any
    ) -> tuple[datetime | None, str | None]:
        """``None``/vacío → sin fecha; ISO ``YYYY-MM-DD`` → mediodía en la zona del usuario."""
        if raw is None or not str(raw).strip():
            return None, None
        try:
            day = date.fromisoformat(str(raw).strip())
        except ValueError:
            return None, "La 'fecha_limite' debe ir en formato ISO 'YYYY-MM-DD'."
        return datetime.combine(day, time(12, 0), tzinfo=self._tz), None


def _optional(raw: Any) -> str | None:
    """Texto opcional: ``None``/vacío → ``None``; en otro caso el string recortado."""
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None

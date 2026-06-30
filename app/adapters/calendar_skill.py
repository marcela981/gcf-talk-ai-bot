"""Skill de agenda (read-only): `consultar_calendario` (ADR-016/ADR-018).

Adapter **delgado** que expone la lectura de calendario impersonado como una *tool*
del agente. A diferencia de la skill de base de conocimiento (app-only), esta skill
**SÍ usa la identidad**: requiere ``actor.impersonated_uid``; si es ``None`` (invitado
o usuario sin identidad local, ADR-016) se **rehúsa** con un `SkillResult` de error
claro. Si hay identidad, delega en `CalendarPort` y devuelve los eventos del día.

* **READ-ONLY**: solo lista eventos; no crea ni modifica (la escritura impersonada
  no está validada — SPIKE_IMPERSONATION §6).
* El I/O vive en el `CalendarPort` inyectado; ``execute`` queda delgado (ADR-018).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone, tzinfo
from typing import Any

from app.domain.actor_context import ActorContext
from app.domain.calendar import CalendarEvent, DateRange
from app.domain.skill_result import SkillResult
from app.services.calendar_port import CalendarPort

logger = logging.getLogger(__name__)

_NAME = "consultar_calendario"
_DESCRIPTION = (
    "Consulta los eventos del calendario del usuario que te escribe, para un día "
    "concreto. Úsala cuando pregunte por su agenda o su disponibilidad, p. ej.: "
    "'¿qué tengo hoy?', 'resúmeme mi día', '¿qué reuniones tengo mañana?', "
    "'¿estoy libre el martes?'. Devuelve la lista de eventos (título, inicio, fin) "
    "del día indicado. SOLO lectura: no crea ni modifica eventos. Para un día "
    "distinto de hoy, pasa la fecha resuelta en 'fecha'."
)
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fecha": {
            "type": "string",
            "description": (
                "Día a consultar en formato ISO 'YYYY-MM-DD'. Resuelve tú "
                "expresiones como 'hoy', 'mañana' o 'el martes' a la fecha "
                "concreta antes de llamar. Omítela para consultar el día de hoy."
            ),
        }
    },
    "additionalProperties": False,
}

_NO_IDENTITY_MSG = (
    "Acción no disponible para invitados o usuarios sin identidad local: solo "
    "puedo consultar el calendario de usuarios de Nextcloud."
)


class ResumenAgendaSkill:
    """Implementa el contrato `Skill` delegando la lectura en un `CalendarPort`.

    ``tz`` es la zona horaria del usuario (de ``settings.bot_default_tz``): define
    qué es "hoy" y en qué hora local se presentan los eventos al LLM (Bloque 2.1).
    """

    def __init__(self, *, calendar: CalendarPort, tz: tzinfo = timezone.utc) -> None:
        self._calendar = calendar
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
        """Rehúsa sin identidad; si la hay, lista los eventos del día via `CalendarPort`."""
        if actor.impersonated_uid is None:
            return SkillResult.failure(_NO_IDENTITY_MSG)

        day = _parse_day(args.get("fecha"), self._tz)
        if day is None:
            return SkillResult.failure(
                "La fecha debe ir en formato ISO 'YYYY-MM-DD'."
            )

        try:
            events = await self._calendar.list_events(
                actor.impersonated_uid, DateRange.for_day(day, tz=self._tz)
            )
        except Exception as exc:  # noqa: BLE001 — devolver el fallo como dato (ADR-018)
            logger.exception("Consulta de calendario falló para el día %s.", day)
            return SkillResult.failure(f"Error consultando el calendario: {exc}")

        return SkillResult.success(
            {
                "fecha": day.isoformat(),
                "zona_horaria": _tz_label(self._tz),
                "total": len(events),
                "eventos": [_event_to_dict(e, self._tz) for e in events],
            }
        )


def _parse_day(raw: Any, tz: tzinfo) -> date | None:
    """``None``/vacío → hoy **en la zona del usuario**; ISO → ese día; inválido → ``None``."""
    if raw is None or not str(raw).strip():
        return datetime.now(tz).date()
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        return None


def _event_to_dict(event: CalendarEvent, tz: tzinfo) -> dict[str, Any]:
    """Serializa el evento con sus horas en **hora local** del usuario.

    Las horas internas son UTC-aware; se convierten a ``tz`` para que el LLM no
    re-interprete husos (la zona se anuncia aparte en ``zona_horaria``).
    """
    return {
        "titulo": event.summary,
        "inicio": event.start.astimezone(tz).isoformat(),
        "fin": event.end.astimezone(tz).isoformat() if event.end is not None else None,
        "todo_el_dia": event.all_day,
        "calendario": event.calendar,
    }


def _tz_label(tz: tzinfo) -> str:
    """Nombre legible de la zona (``key`` de ZoneInfo, p. ej. 'America/Bogota')."""
    return getattr(tz, "key", None) or str(tz)

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
from typing import Any, Callable

from app.domain.actor_context import ActorContext
from app.domain.calendar import CalendarEvent, DateRange
from app.domain.skill_result import SkillResult
from app.services.calendar_port import CalendarPort

logger = logging.getLogger(__name__)

_NAME = "consultar_calendario"
_DESCRIPTION = (
    "Consulta los eventos del calendario del usuario que te escribe, para un día o "
    "un rango de días. Incluye las ocurrencias de eventos recurrentes (reuniones "
    "semanales, etc.). Úsala cuando pregunte por su agenda o disponibilidad, p. ej.: "
    "'¿qué tengo hoy?', 'resúmeme mi día', '¿qué reuniones tengo mañana?', "
    "'¿estoy libre el martes?', '¿qué tengo esta semana?', 'mis próximos 10 días'. "
    "Devuelve la lista de eventos (título, inicio, fin). SOLO lectura: no crea ni "
    "modifica eventos.\n"
    "FECHAS: para HOY, OMITE 'fecha' y 'fecha_fin' (el sistema usa la fecha real; no "
    "la inventes). Para un día distinto pasa solo 'fecha' (ISO YYYY-MM-DD). Para un "
    "RANGO pasa 'fecha' (inicio) y 'fecha_fin' (fin, inclusive): p. ej. 'esta semana' "
    "o 'próximos 14 días' → fecha=hoy y fecha_fin=hoy+14. Calcula SIEMPRE las fechas "
    "a partir de la 'Fecha y hora actuales' del contexto, nunca de tu conocimiento "
    "previo."
)
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fecha": {
            "type": "string",
            "description": (
                "Día a consultar (o inicio del rango) en formato ISO 'YYYY-MM-DD'. "
                "OMÍTELA para hoy. Calcúlala desde la 'Fecha y hora actuales' del "
                "contexto (p. ej. 'mañana' = ese día + 1)."
            ),
        },
        "fecha_fin": {
            "type": "string",
            "description": (
                "Fin del rango en ISO 'YYYY-MM-DD', INCLUSIVE. Inclúyela solo para "
                "rangos de varios días (p. ej. 'esta semana', 'próximos N días'); "
                "debe ser >= 'fecha'. Omítela para consultar un único día."
            ),
        },
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
    ``now_fn`` es el reloj (inyectable para tests); por defecto el reloj real en
    ``tz``. "Hoy" lo decide SIEMPRE el código con este reloj, nunca el LLM.
    """

    def __init__(
        self,
        *,
        calendar: CalendarPort,
        tz: tzinfo = timezone.utc,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._calendar = calendar
        self._tz = tz
        self._now_fn = now_fn

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
        """Rehúsa sin identidad; si la hay, lista eventos del día/rango via `CalendarPort`."""
        if actor.impersonated_uid is None:
            return SkillResult.failure(_NO_IDENTITY_MSG)

        # Inicio del rango: 'fecha', o HOY (decidido por el CÓDIGO con el reloj).
        start_day = _parse_day(args.get("fecha"), self._tz, self._now_fn)
        if start_day is None:
            return SkillResult.failure("La fecha debe ir en formato ISO 'YYYY-MM-DD'.")

        # Fin del rango: 'fecha_fin' (inclusive) o el mismo día (consulta de un día).
        end_raw = args.get("fecha_fin")
        if end_raw is None or not str(end_raw).strip():
            end_day = start_day
        else:
            end_day = _parse_iso(end_raw)
            if end_day is None:
                return SkillResult.failure(
                    "La fecha_fin debe ir en formato ISO 'YYYY-MM-DD'."
                )
            if end_day < start_day:
                return SkillResult.failure(
                    "El rango es inválido: 'fecha_fin' no puede ser anterior a 'fecha'."
                )

        try:
            events = await self._calendar.list_events(
                actor.impersonated_uid,
                DateRange.for_range(start_day, end_day, tz=self._tz),
            )
        except Exception as exc:  # noqa: BLE001 — devolver el fallo como dato (ADR-018)
            logger.exception(
                "Consulta de calendario falló para %s..%s.", start_day, end_day
            )
            return SkillResult.failure(f"Error consultando el calendario: {exc}")

        return SkillResult.success(
            {
                "desde": start_day.isoformat(),
                "hasta": end_day.isoformat(),
                "zona_horaria": _tz_label(self._tz),
                "total": len(events),
                "eventos": [_event_to_dict(e, self._tz) for e in events],
            }
        )


def _parse_day(
    raw: Any, tz: tzinfo, now_fn: Callable[[], datetime] | None = None
) -> date | None:
    """``None``/vacío → HOY en la zona del usuario (decidido por el CÓDIGO, nunca por
    el LLM); ISO ``YYYY-MM-DD`` → ese día; inválido → ``None``."""
    if raw is None or not str(raw).strip():
        now = now_fn() if now_fn is not None else datetime.now(tz)
        return now.date()
    return _parse_iso(raw)


def _parse_iso(raw: Any) -> date | None:
    """ISO ``YYYY-MM-DD`` → ``date``; cualquier otra cosa → ``None``."""
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

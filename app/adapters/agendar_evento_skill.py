"""Skill de escritura: `agendar_evento` (ADR-016/ADR-018, Bloque 2.2).

DECISIÓN — skill **NUEVA** en vez de una acción más en `ResumenAgendaSkill` (opción (b)
del bloque): lectura ("¿qué tengo?") y escritura ("agéndame…") son **intenciones
distintas ante el LLM**. Separarlas da a cada una su ``name``/``description`` propios, lo
que el modelo enruta mejor por tool-calling (SRP: una skill, una intención). Ambas
comparten el mismo `CalendarPort` inyectado y el mismo gate ``appapi_ready`` en el
composition root; el port ya distingue ``list_events`` de ``create_event`` (OCP aditivo).

Como la de lectura, **usa la identidad**: requiere ``actor.impersonated_uid``; si es
``None`` (invitado/federado, ADR-016) se **rehúsa** con un `SkillResult` de error claro,
SIN tocar el calendario. Es la primera skill con **efectos** (SPIKE_IMPERSONATION §6).
El I/O vive en el `CalendarPort`; ``execute`` queda delgado (ADR-018): valida los args,
arma un `NewCalendarEvent` con horas **tz-aware** en la zona del usuario y delega.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any

from app.domain.actor_context import ActorContext
from app.domain.calendar import NewCalendarEvent
from app.domain.skill_result import SkillResult
from app.services.calendar_port import CalendarPort

logger = logging.getLogger(__name__)

_NAME = "agendar_evento"
_DEFAULT_DURATION_MIN = 60
_DESCRIPTION = (
    "Crea (agenda) un evento en el calendario del usuario que te escribe. Úsala cuando "
    "pida crear, agendar, programar o reservar una cita/reunión/recordatorio, p. ej.: "
    "'agéndame una reunión mañana a las 3pm', 'crea un evento el viernes de 9 a 10', "
    "'resérvame el martes de 2 a 3 para el cliente'. Requiere 'titulo', 'fecha' y "
    "'hora_inicio'; el fin va por 'hora_fin' o 'duracion' (minutos) y, si faltan ambas, "
    "se asume 1 hora. Devuelve confirmación con el identificador del evento creado. "
    "SOLO crea eventos con hora; no lista ni modifica (para consultar la agenda usa "
    "'consultar_calendario').\n"
    "FECHAS/HORAS: calcula SIEMPRE 'fecha' (ISO 'YYYY-MM-DD') y las horas ('HH:MM', 24h) "
    "a partir de la 'Fecha y hora actuales' del contexto, nunca de tu conocimiento "
    "previo. Las horas se interpretan en la zona horaria del usuario."
)
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "titulo": {
            "type": "string",
            "description": "Título del evento. Obligatorio.",
        },
        "fecha": {
            "type": "string",
            "description": (
                "Día del evento en ISO 'YYYY-MM-DD'. Calcúlalo desde la 'Fecha y hora "
                "actuales' del contexto (p. ej. 'mañana' = ese día + 1)."
            ),
        },
        "hora_inicio": {
            "type": "string",
            "description": "Hora de inicio 'HH:MM' (24h), en la zona del usuario.",
        },
        "hora_fin": {
            "type": "string",
            "description": (
                "Hora de fin 'HH:MM' (24h), posterior a 'hora_inicio'. Alternativa a "
                "'duracion'; si das ambas, se usa 'hora_fin'."
            ),
        },
        "duracion": {
            "type": "integer",
            "description": (
                "Duración del evento EN MINUTOS (alternativa a 'hora_fin'). Si no das ni "
                "'hora_fin' ni 'duracion', se asume 60."
            ),
        },
        "descripcion": {
            "type": "string",
            "description": "Descripción o notas del evento (opcional).",
        },
        "ubicacion": {
            "type": "string",
            "description": "Ubicación del evento (opcional).",
        },
    },
    "required": ["titulo", "fecha", "hora_inicio"],
    "additionalProperties": False,
}

_NO_IDENTITY_MSG = (
    "Acción no disponible para invitados o usuarios sin identidad local: solo "
    "puedo crear eventos en el calendario de usuarios de Nextcloud."
)


class AgendarEventoSkill:
    """Implementa el contrato `Skill` delegando la creación en un `CalendarPort`.

    ``tz`` es la zona horaria del usuario (de ``settings.bot_default_tz``): las horas de
    los args se interpretan en esa zona (Bloque 2.1). "Hoy" no aplica aquí — la 'fecha'
    es obligatoria y la resuelve el LLM desde la fecha real inyectada en el contexto.
    """

    def __init__(
        self,
        *,
        calendar: CalendarPort,
        tz: tzinfo = timezone.utc,
    ) -> None:
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
        """Rehúsa sin identidad; si la hay, valida los args y crea el evento via `CalendarPort`."""
        if actor.impersonated_uid is None:
            return SkillResult.failure(_NO_IDENTITY_MSG)

        titulo = str(args.get("titulo") or "").strip()
        if not titulo:
            return SkillResult.failure("Falta el 'titulo' del evento.")

        event_day = _parse_iso(args.get("fecha"))
        if event_day is None:
            return SkillResult.failure("La 'fecha' debe ir en formato ISO 'YYYY-MM-DD'.")

        start_time = _parse_hhmm(args.get("hora_inicio"))
        if start_time is None:
            return SkillResult.failure("La 'hora_inicio' debe ir en formato 'HH:MM' (24h).")
        start = datetime.combine(event_day, start_time, tzinfo=self._tz)

        end, error = self._resolve_end(args, event_day, start)
        if error is not None:
            return SkillResult.failure(error)

        draft = NewCalendarEvent(
            summary=titulo,
            start=start,
            end=end,
            description=_optional(args.get("descripcion")),
            location=_optional(args.get("ubicacion")),
        )

        try:
            result = await self._calendar.create_event(actor.impersonated_uid, draft)
        except Exception as exc:  # noqa: BLE001 — devolver el fallo como dato (ADR-018)
            logger.exception(
                "Creación de evento falló para %s (%s).",
                actor.impersonated_uid,
                titulo,
            )
            return SkillResult.failure(f"Error creando el evento: {exc}")

        if not result.ok:
            return SkillResult.failure(result.error or "No se pudo crear el evento.")

        return SkillResult.success(
            {
                "creado": True,
                "titulo": titulo,
                "inicio": start.isoformat(),
                "fin": end.isoformat(),
                "calendario": result.calendar,
                "uid": result.uid,
                "href": result.href,
                "zona_horaria": _tz_label(self._tz),
            }
        )

    def _resolve_end(
        self, args: dict[str, Any], event_day: date, start: datetime
    ) -> tuple[datetime | None, str | None]:
        """Fin del evento: 'hora_fin' (preferente) o 'duracion' (min) o el default de 60 min.

        Devuelve ``(end, None)`` en éxito o ``(None, error)`` con un mensaje claro.
        """
        hora_fin_raw = args.get("hora_fin")
        if hora_fin_raw is not None and str(hora_fin_raw).strip():
            end_time = _parse_hhmm(hora_fin_raw)
            if end_time is None:
                return None, "La 'hora_fin' debe ir en formato 'HH:MM' (24h)."
            end = datetime.combine(event_day, end_time, tzinfo=self._tz)
            if end <= start:
                return None, "La 'hora_fin' debe ser posterior a 'hora_inicio'."
            return end, None

        duracion_raw = args.get("duracion")
        if duracion_raw is not None and str(duracion_raw).strip():
            try:
                minutes = int(duracion_raw)
            except (TypeError, ValueError):
                return None, "La 'duracion' debe ser un número de minutos."
            if minutes <= 0:
                return None, "La 'duracion' debe ser mayor que 0 minutos."
            return start + timedelta(minutes=minutes), None

        # Ni 'hora_fin' ni 'duracion': se asume 1 hora (documentado en la description).
        return start + timedelta(minutes=_DEFAULT_DURATION_MIN), None


def _parse_iso(raw: Any) -> date | None:
    """ISO ``YYYY-MM-DD`` → ``date``; cualquier otra cosa → ``None``."""
    try:
        return date.fromisoformat(str(raw).strip())
    except (ValueError, TypeError):
        return None


def _parse_hhmm(raw: Any) -> time | None:
    """``HH:MM`` (o ``HH:MM:SS``) → ``time``; admite hora de 1-2 dígitos; inválido → ``None``."""
    if raw is None:
        return None
    text = str(raw).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def _optional(raw: Any) -> str | None:
    """Texto opcional: ``None``/vacío → ``None``; en otro caso el string recortado."""
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _tz_label(tz: tzinfo) -> str:
    """Nombre legible de la zona (``key`` de ZoneInfo, p. ej. 'America/Bogota')."""
    return getattr(tz, "key", None) or str(tz)

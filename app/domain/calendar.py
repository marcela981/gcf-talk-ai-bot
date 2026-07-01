"""Value objects de calendario (dominio puro, sin I/O).

``CalendarEvent`` es un evento normalizado que la skill de agenda devuelve al LLM;
``DateRange`` acota la ventana temporal de una consulta. Sin dependencias de
framework ni red: el adapter CalDAV (infra) produce estos tipos a partir del
multistatus; la skill (`services`/`adapters`) los consume. El puerto
``CalendarPort`` habla en estos términos, no en XML.

ZONA HORARIA (Bloque 2.1): TODO se maneja **UTC-aware** internamente, pero el "día"
se define **en la zona del usuario** (no en UTC). ``DateRange`` recuerda esa zona
(``tz``) para que el adapter sepa con qué zona interpretar las horas flotantes del
iCal y para presentar las horas en local. La comparación de pertenencia al día es
aware-vs-aware (:meth:`DateRange.contains`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def to_zoneinfo(name: str, *, default: tzinfo = timezone.utc) -> tzinfo:
    """Resuelve un nombre IANA a ``tzinfo`` con `zoneinfo`; ante nombre inválido, ``default``.

    Degradación deliberada: una ``BOT_DEFAULT_TZ`` mal escrita o un ``TZID``
    desconocido en un iCal NO deben tumbar la consulta — se cae a ``default`` (UTC
    o la zona del usuario, según el llamador).
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return default


@dataclass(frozen=True)
class DateRange:
    """Ventana ``[start, end)`` en instantes **UTC-aware**, framed en la zona ``tz``.

    * ``start``/``end`` — instantes UTC-aware (``start`` inclusivo, ``end`` exclusivo).
    * ``tz``           — zona del usuario que definió el "día" (para parsear horas
      flotantes del iCal y para presentar en local).
    """

    start: datetime
    end: datetime
    tz: tzinfo = timezone.utc

    @classmethod
    def for_day(cls, day: date, *, tz: tzinfo = timezone.utc) -> "DateRange":
        """Rango del día completo **en la zona ``tz``**: ``[00:00 local, 24:00 local)``.

        Helper del caso "un día" (atajo de :meth:`for_range` con el mismo día como
        inicio y fin). Las fronteras son medianoche local del día y del siguiente,
        expuestas en UTC para filtrar contra instantes aware.
        """
        return cls.for_range(day, day, tz=tz)

    @classmethod
    def for_range(
        cls, start_day: date, end_day: date, *, tz: tzinfo = timezone.utc
    ) -> "DateRange":
        """Rango ``[start_day 00:00 local, end_day+1 00:00 local)`` en la zona ``tz``.

        Ambos extremos son **inclusivos por día**: ``for_range(d, d)`` cubre justo
        ese día; ``for_range(hoy, hoy+14d)`` cubre los 15 días (hoy..hoy+14). Las
        fronteras se construyen como medianoche local y se exponen en UTC.
        """
        start_local = datetime.combine(start_day, time.min, tzinfo=tz)
        end_local = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=tz)
        return cls(
            start=start_local.astimezone(timezone.utc),
            end=end_local.astimezone(timezone.utc),
            tz=tz,
        )

    def contains(self, instant: datetime) -> bool:
        """``True`` si el instante (aware) cae en ``[start, end)``. Compara aware vs aware."""
        return self.start <= instant < self.end


@dataclass(frozen=True)
class CalendarEvent:
    """Un evento de calendario ya normalizado desde iCalendar.

    * ``summary``   — título del evento.
    * ``start``     — inicio, SIEMPRE **UTC-aware** (para todo-el-día, la medianoche
      local del día convertida a UTC).
    * ``end``       — fin UTC-aware, o ``None`` si el VEVENT no lo trae.
    * ``all_day``   — ``True`` para eventos ``VALUE=DATE`` (sin hora).
    * ``calendar``  — pista del calendario de origen (último segmento del href).
    * ``recurring`` — ``True`` si el VEVENT traía un ``RRULE`` (es el *maestro* de
      una serie SIN expandir). Las ocurrencias expandidas por el servidor llegan
      como instancias concretas con ``recurring=False``. Sirve de señal de que la
      expansión server-side (``<C:expand>``) NO se honró (ver el adapter CalDAV).
    """

    summary: str
    start: datetime
    end: datetime | None = None
    all_day: bool = False
    calendar: str | None = None
    recurring: bool = False


@dataclass(frozen=True)
class NewCalendarEvent:
    """Un evento **a crear** (draft), en la zona del usuario (Bloque 2.2). Value object puro.

    Es la entrada de :meth:`~app.services.calendar_port.CalendarPort.create_event`:
    describe el *contenido* del evento, no dónde se guarda salvo el ``calendar``
    opcional (segmento destino; ``None`` ⇒ el adapter usa el calendario ``personal``).

    * ``summary``     — título (obligatorio).
    * ``start``/``end`` — instantes **tz-aware** en la zona del usuario (``end`` exclusivo,
      posterior a ``start``). El adapter los serializa a iCal con ``TZID`` + ``VTIMEZONE``.
    * ``description``/``location`` — texto opcional.
    * ``calendar``    — nombre del calendario destino (último segmento del href, p. ej.
      ``"work"``); ``None`` ⇒ ``personal`` (default del adapter).

    NO valida aquí (mismo estilo que :class:`CalendarEvent`): la skill valida los args
    y construye horas aware antes de instanciar; el adapter rechaza uid vacío.
    """

    summary: str
    start: datetime
    end: datetime
    description: str | None = None
    location: str | None = None
    calendar: str | None = None


@dataclass(frozen=True)
class CreatedEvent:
    """Resultado de crear un evento (Bloque 2.2). Value object puro, **error como dato**.

    El adapter NO lanza excepción cruda ante los rechazos HTTP esperables de escritura
    (403/409/412): los devuelve como ``ok=False`` + ``error`` legible, para que la skill
    los traduzca a ``SkillResult.failure`` y el LLM pueda explicárselo al usuario
    (SPIKE_IMPERSONATION §6: esta es la primera validación de escritura impersonada).

    * ``ok``       — ``True`` sii el servidor confirmó la creación (HTTP 201/204).
    * ``status``   — código HTTP crudo devuelto por el PUT.
    * ``uid``      — UID del VEVENT generado (también el nombre del recurso ``.ics``).
    * ``calendar`` — segmento del calendario destino usado.
    * ``href``     — ruta del recurso creado (``Location`` del 201 o la ruta del PUT);
      ``None`` si no se creó.
    * ``error``    — mensaje claro cuando ``ok=False``; ``None`` en éxito.
    """

    ok: bool
    status: int
    uid: str
    calendar: str
    href: str | None = None
    error: str | None = None

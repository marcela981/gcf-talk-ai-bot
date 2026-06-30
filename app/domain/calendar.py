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

        Las fronteras se construyen como medianoche local del día y del día
        siguiente, y se exponen convertidas a UTC para filtrar contra instantes
        aware. Nada de fronteras naive ni en UTC fijo (corrige la deuda de
        encuadre temporal del Bloque 2).
        """
        start_local = datetime.combine(day, time.min, tzinfo=tz)
        end_local = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
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

    * ``summary``  — título del evento.
    * ``start``    — inicio, SIEMPRE **UTC-aware** (para todo-el-día, la medianoche
      local del día convertida a UTC).
    * ``end``      — fin UTC-aware, o ``None`` si el VEVENT no lo trae.
    * ``all_day``  — ``True`` para eventos ``VALUE=DATE`` (sin hora).
    * ``calendar`` — pista del calendario de origen (último segmento del href).
    """

    summary: str
    start: datetime
    end: datetime | None = None
    all_day: bool = False
    calendar: str | None = None

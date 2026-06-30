"""Value objects de calendario (dominio puro, sin I/O).

``CalendarEvent`` es un evento normalizado que la skill de agenda devuelve al LLM;
``DateRange`` acota la ventana temporal de una consulta. Sin dependencias de
framework ni red: el adapter CalDAV (infra) produce estos tipos a partir del
multistatus; la skill (`services`/`adapters`) los consume. El puerto
``CalendarPort`` habla en estos términos, no en XML.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone


@dataclass(frozen=True)
class DateRange:
    """Ventana ``[start, end)``: `start` inclusivo, `end` exclusivo (aware/UTC)."""

    start: datetime
    end: datetime

    @classmethod
    def for_day(cls, day: date, *, tz: timezone = timezone.utc) -> "DateRange":
        """Rango que cubre el día completo ``[día 00:00, día+1 00:00)``.

        BLOQUE 2 — simplificación: las fronteras se toman en ``tz`` (UTC por
        defecto) porque aún no derivamos la zona horaria del usuario (trabajo
        futuro, ADR-016/ADR-011). Los eventos se devuelven con su hora tal cual.
        """
        start = datetime.combine(day, time.min, tzinfo=tz)
        return cls(start=start, end=start + timedelta(days=1))


@dataclass(frozen=True)
class CalendarEvent:
    """Un evento de calendario ya normalizado desde iCalendar.

    * ``summary``  — título del evento.
    * ``start``    — inicio (datetime; para todo-el-día, medianoche).
    * ``end``      — fin, o ``None`` si el VEVENT no lo trae.
    * ``all_day``  — ``True`` para eventos ``VALUE=DATE`` (sin hora).
    * ``calendar`` — pista del calendario de origen (último segmento del href).
    """

    summary: str
    start: datetime
    end: datetime | None = None
    all_day: bool = False
    calendar: str | None = None

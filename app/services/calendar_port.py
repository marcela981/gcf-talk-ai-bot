"""Contrato `CalendarPort` (ADR-016/ADR-018): calendario impersonado.

Lectura (:meth:`~CalendarPort.list_events`, Bloque 2) **más** creación de eventos
(:meth:`~CalendarPort.create_event`, Bloque 2.2). La adición de escritura es
**aditiva** (OCP): ``list_events`` no cambia su firma ni su semántica. La escritura
impersonada quedó **sin validar** en el spike de lectura (ver
``docs/spikes/SPIKE_IMPERSONATION.md`` §6); el Bloque 2.2 es justo esa validación, con
el primer ``create_event`` real. Las skills dependen de esta interfaz, no del adapter
concreto; el adapter de Nextcloud (CalDAV) la implementa en ``adapters/``. Sin
dependencias de framework (regla de capas, ARCHITECTURE §3): el contrato vive en
``services`` y habla en value objects de dominio.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.calendar import (
    CalendarEvent,
    CreatedEvent,
    DateRange,
    NewCalendarEvent,
)


@runtime_checkable
class CalendarPort(Protocol):
    """Acceso al calendario de un usuario, bajo SU identidad (lectura + creación)."""

    async def list_events(
        self, uid: str, date_range: DateRange
    ) -> list[CalendarEvent]:
        """Eventos del usuario ``uid`` cuyo inicio cae en ``date_range`` ``[start, end)``.

        El ``DateRange`` (UTC-aware, framed en la tz del usuario) puede cubrir un
        solo día (:meth:`DateRange.for_day`) o varios (:meth:`DateRange.for_range`),
        p. ej. "esta semana" o "próximos N días". La implementación incluye las
        **ocurrencias de eventos recurrentes** dentro del rango (expansión), no solo
        los eventos maestros.

        Actúa **impersonando** a ``uid`` (ADR-016). Puede lanzar un error propio del
        adapter ante fallo de transporte/HTTP; el llamador (la skill) lo traduce a un
        ``SkillResult.failure`` para el loop (ADR-017).
        """
        ...

    async def create_event(self, uid: str, event: NewCalendarEvent) -> CreatedEvent:
        """Crea ``event`` en el calendario de ``uid`` (Bloque 2.2), **impersonando** a ``uid``.

        Destino: ``event.calendar`` (segmento del href) o el calendario ``personal`` por
        defecto. Devuelve un :class:`CreatedEvent`: ``ok=True`` con ``href``/``uid`` cuando
        el servidor confirma (HTTP 201/204); ``ok=False`` con un ``error`` legible ante los
        rechazos esperables de escritura (403/409/412) — el fallo es **dato, no excepción
        cruda** (el llamador lo traduce a ``SkillResult.failure``). Un fallo de
        transporte/HTTP inesperado sí puede propagarse como excepción del adapter.
        """
        ...

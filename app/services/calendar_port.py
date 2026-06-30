"""Contrato `CalendarPort` (ADR-016/ADR-018): lectura de calendario impersonado.

Puerto **SOLO LECTURA** por ahora (Bloque 2): no expone crear/mover/borrar — la
escritura impersonada **no** está validada (ver ``docs/spikes/SPIKE_IMPERSONATION.md``
§6). La skill de agenda depende de esta interfaz, no del adapter concreto; el
adapter de Nextcloud (CalDAV) la implementa en ``adapters/``. Sin dependencias de
framework (regla de capas, ARCHITECTURE §3): el contrato vive en ``services``.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.calendar import CalendarEvent, DateRange


@runtime_checkable
class CalendarPort(Protocol):
    """Acceso de solo-lectura al calendario de un usuario, bajo SU identidad."""

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

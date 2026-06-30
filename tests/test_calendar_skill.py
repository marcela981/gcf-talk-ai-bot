"""Unit tests for app.adapters.calendar_skill (ResumenAgendaSkill, ADR-016/018).

El `CalendarPort` se reemplaza por un `FakeCalendar` — sin red. Se verifica que la
skill: se REHÚSA cuando no hay identidad impersonable (uid None), delega en el port
con el uid resuelto y el rango del día, usa hoy por defecto, valida la fecha y
convierte fallos del port en `SkillResult.failure` (dato, no excepción).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.adapters.calendar_skill import ResumenAgendaSkill
from app.domain.actor_context import ActorContext
from app.domain.calendar import CalendarEvent, DateRange


class FakeCalendar:
    def __init__(self, events: list[CalendarEvent] | None = None) -> None:
        self._events = events or []
        self.calls: list[tuple[str, DateRange]] = []

    async def list_events(self, uid: str, date_range: DateRange) -> list[CalendarEvent]:
        self.calls.append((uid, date_range))
        return list(self._events)


class BoomCalendar:
    async def list_events(self, uid: str, date_range: DateRange) -> list[CalendarEvent]:
        raise RuntimeError("CalDAV caído")


_USER = ActorContext(actor_id="users/mmazo", token="room1", impersonated_uid="mmazo")
_GUEST = ActorContext(actor_id="guests/abc", token="room1", impersonated_uid=None)


@pytest.mark.asyncio
async def test_refuses_without_local_identity():
    calendar = FakeCalendar()
    skill = ResumenAgendaSkill(calendar=calendar)

    result = await skill.execute({"fecha": "2026-06-30"}, _GUEST)

    assert not result.ok
    assert "invitados" in result.error
    # No debe haber tocado el calendario.
    assert calendar.calls == []


@pytest.mark.asyncio
async def test_delegates_with_resolved_uid_and_day_range():
    events = [
        CalendarEvent(
            summary="Daily",
            start=datetime(2026, 6, 30, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 6, 30, 9, 15, tzinfo=timezone.utc),
            calendar="work",
        )
    ]
    calendar = FakeCalendar(events)
    skill = ResumenAgendaSkill(calendar=calendar)

    result = await skill.execute({"fecha": "2026-06-30"}, _USER)

    assert result.ok
    assert result.data["fecha"] == "2026-06-30"
    assert result.data["total"] == 1
    assert result.data["eventos"][0]["titulo"] == "Daily"
    assert result.data["eventos"][0]["inicio"] == "2026-06-30T09:00:00+00:00"
    # Se delegó con el uid resuelto y el rango del día pedido.
    uid, rng = calendar.calls[0]
    assert uid == "mmazo"
    assert rng == DateRange.for_day(date(2026, 6, 30))


@pytest.mark.asyncio
async def test_defaults_to_today_when_fecha_absent():
    calendar = FakeCalendar()
    skill = ResumenAgendaSkill(calendar=calendar)

    result = await skill.execute({}, _USER)

    assert result.ok
    assert result.data["fecha"] == date.today().isoformat()
    _, rng = calendar.calls[0]
    assert rng == DateRange.for_day(date.today())


@pytest.mark.asyncio
async def test_invalid_date_is_a_failure():
    calendar = FakeCalendar()
    skill = ResumenAgendaSkill(calendar=calendar)

    result = await skill.execute({"fecha": "30/06/2026"}, _USER)

    assert not result.ok
    assert "ISO" in result.error
    assert calendar.calls == []  # no se llega al port


@pytest.mark.asyncio
async def test_port_error_becomes_failure_result_not_exception():
    skill = ResumenAgendaSkill(calendar=BoomCalendar())

    result = await skill.execute({"fecha": "2026-06-30"}, _USER)

    assert not result.ok
    assert "calendario" in result.error.lower()


def test_tool_schema_is_public_contract():
    skill = ResumenAgendaSkill(calendar=FakeCalendar())

    assert skill.name == "consultar_calendario"
    assert skill.parameters_schema["additionalProperties"] is False
    assert "fecha" in skill.parameters_schema["properties"]

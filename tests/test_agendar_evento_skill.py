"""Unit tests para AgendarEventoSkill (escritura, ADR-016/018, Bloque 2.2).

El `CalendarPort` se reemplaza por un `FakeCalendar` — sin red. Se verifica que la skill:
se REHÚSA sin identidad impersonable (uid None), valida los args (titulo/fecha/horas),
arma el `NewCalendarEvent` con horas tz-aware EN LA ZONA del usuario, resuelve el fin por
'hora_fin' / 'duracion' / default 60 min, y convierte tanto un `CreatedEvent` de error del
port (p. ej. 403) como una excepción en `SkillResult.failure` (dato, no excepción).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.adapters.agendar_evento_skill import AgendarEventoSkill
from app.domain.actor_context import ActorContext
from app.domain.calendar import CreatedEvent, NewCalendarEvent

BOGOTA = ZoneInfo("America/Bogota")  # UTC-5, sin DST

_USER = ActorContext(actor_id="users/mmazo", token="room1", impersonated_uid="mmazo")
_GUEST = ActorContext(actor_id="guests/abc", token="room1", impersonated_uid=None)


class FakeCalendar:
    def __init__(self, result: CreatedEvent | None = None) -> None:
        self._result = result
        self.calls: list[tuple[str, NewCalendarEvent]] = []

    async def create_event(self, uid: str, event: NewCalendarEvent) -> CreatedEvent:
        self.calls.append((uid, event))
        if self._result is not None:
            return self._result
        return CreatedEvent(
            ok=True,
            status=201,
            uid="evt-1",
            calendar=event.calendar or "personal",
            href="/remote.php/dav/calendars/mmazo/personal/evt-1.ics",
        )


class BoomCalendar:
    async def create_event(self, uid: str, event: NewCalendarEvent) -> CreatedEvent:
        raise RuntimeError("CalDAV caído")


def _args(**overrides) -> dict:
    base = {"titulo": "Reunión", "fecha": "2026-07-01", "hora_inicio": "09:00"}
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_refuses_without_local_identity():
    calendar = FakeCalendar()
    skill = AgendarEventoSkill(calendar=calendar, tz=BOGOTA)

    result = await skill.execute(_args(), _GUEST)

    assert not result.ok
    assert "invitados" in result.error
    assert calendar.calls == []  # no debe tocar el calendario


@pytest.mark.asyncio
async def test_creates_event_delegating_with_user_zone_times():
    calendar = FakeCalendar()
    skill = AgendarEventoSkill(calendar=calendar, tz=BOGOTA)

    result = await skill.execute(
        _args(
            hora_fin="10:00",
            descripcion="con cliente",
            ubicacion="Sala A",
        ),
        _USER,
    )

    assert result.ok
    assert result.data["creado"] is True
    assert result.data["titulo"] == "Reunión"
    # Las horas se presentan en LOCAL del usuario.
    assert result.data["inicio"] == "2026-07-01T09:00:00-05:00"
    assert result.data["fin"] == "2026-07-01T10:00:00-05:00"
    assert result.data["zona_horaria"] == "America/Bogota"
    assert result.data["calendario"] == "personal"
    assert result.data["uid"] == "evt-1"

    # El draft delegado lleva horas tz-aware en la zona del usuario.
    uid, draft = calendar.calls[0]
    assert uid == "mmazo"
    assert draft.summary == "Reunión"
    assert draft.start == datetime(2026, 7, 1, 9, 0, tzinfo=BOGOTA)
    assert draft.end == datetime(2026, 7, 1, 10, 0, tzinfo=BOGOTA)
    assert draft.start.tzinfo is not None
    assert draft.description == "con cliente"
    assert draft.location == "Sala A"


@pytest.mark.asyncio
async def test_duracion_minutes_used_when_no_hora_fin():
    calendar = FakeCalendar()
    skill = AgendarEventoSkill(calendar=calendar, tz=BOGOTA)

    result = await skill.execute(_args(duracion=30), _USER)

    assert result.ok
    _, draft = calendar.calls[0]
    assert draft.end == datetime(2026, 7, 1, 9, 30, tzinfo=BOGOTA)


@pytest.mark.asyncio
async def test_defaults_to_one_hour_when_no_end_given():
    calendar = FakeCalendar()
    skill = AgendarEventoSkill(calendar=calendar, tz=BOGOTA)

    result = await skill.execute(_args(), _USER)  # sin hora_fin ni duracion

    assert result.ok
    _, draft = calendar.calls[0]
    assert draft.end == datetime(2026, 7, 1, 10, 0, tzinfo=BOGOTA)


@pytest.mark.asyncio
async def test_hora_fin_wins_over_duracion():
    calendar = FakeCalendar()
    skill = AgendarEventoSkill(calendar=calendar, tz=BOGOTA)

    result = await skill.execute(_args(hora_fin="11:00", duracion=15), _USER)

    assert result.ok
    _, draft = calendar.calls[0]
    assert draft.end == datetime(2026, 7, 1, 11, 0, tzinfo=BOGOTA)


@pytest.mark.asyncio
async def test_missing_titulo_is_failure():
    calendar = FakeCalendar()
    skill = AgendarEventoSkill(calendar=calendar, tz=BOGOTA)

    result = await skill.execute(
        {"fecha": "2026-07-01", "hora_inicio": "09:00"}, _USER
    )

    assert not result.ok
    assert "titulo" in result.error
    assert calendar.calls == []


@pytest.mark.asyncio
async def test_invalid_fecha_is_failure():
    calendar = FakeCalendar()
    skill = AgendarEventoSkill(calendar=calendar, tz=BOGOTA)

    result = await skill.execute(_args(fecha="01/07/2026"), _USER)

    assert not result.ok
    assert "fecha" in result.error.lower()
    assert calendar.calls == []


@pytest.mark.asyncio
async def test_invalid_hora_inicio_is_failure():
    calendar = FakeCalendar()
    skill = AgendarEventoSkill(calendar=calendar, tz=BOGOTA)

    result = await skill.execute(_args(hora_inicio="9am"), _USER)

    assert not result.ok
    assert "hora_inicio" in result.error
    assert calendar.calls == []


@pytest.mark.asyncio
async def test_hora_fin_not_after_inicio_is_failure():
    calendar = FakeCalendar()
    skill = AgendarEventoSkill(calendar=calendar, tz=BOGOTA)

    result = await skill.execute(_args(hora_inicio="10:00", hora_fin="09:00"), _USER)

    assert not result.ok
    assert "posterior" in result.error.lower()
    assert calendar.calls == []


@pytest.mark.asyncio
async def test_invalid_duracion_is_failure():
    calendar = FakeCalendar()
    skill = AgendarEventoSkill(calendar=calendar, tz=BOGOTA)

    result = await skill.execute(_args(duracion="media hora"), _USER)

    assert not result.ok
    assert "duracion" in result.error.lower()
    assert calendar.calls == []


@pytest.mark.asyncio
async def test_port_error_result_becomes_failure():
    # El port devuelve un CreatedEvent de error (p. ej. 403): la skill lo traduce a
    # SkillResult.failure — el "403 -> SkillResult de error" visto de punta a punta.
    calendar = FakeCalendar(
        result=CreatedEvent(
            ok=False,
            status=403,
            uid="evt-x",
            calendar="personal",
            error="No se pudo crear el evento (HTTP 403): posible falta de permiso.",
        )
    )
    skill = AgendarEventoSkill(calendar=calendar, tz=BOGOTA)

    result = await skill.execute(_args(), _USER)

    assert not result.ok
    assert "403" in result.error


@pytest.mark.asyncio
async def test_port_exception_becomes_failure_not_raised():
    skill = AgendarEventoSkill(calendar=BoomCalendar(), tz=BOGOTA)

    result = await skill.execute(_args(), _USER)

    assert not result.ok
    assert "evento" in result.error.lower()


def test_tool_schema_is_public_contract():
    skill = AgendarEventoSkill(calendar=FakeCalendar())

    assert skill.name == "agendar_evento"
    schema = skill.parameters_schema
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["titulo", "fecha", "hora_inicio"]
    props = schema["properties"]
    for key in (
        "titulo",
        "fecha",
        "hora_inicio",
        "hora_fin",
        "duracion",
        "descripcion",
        "ubicacion",
    ):
        assert key in props

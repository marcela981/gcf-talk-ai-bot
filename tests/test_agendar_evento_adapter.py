"""Unit tests para la ESCRITURA de NextcloudCalendarAdapter (Bloque 2.2), sin red.

Se inyecta un `httpx.MockTransport` (mismo patrón que `test_nextcloud_calendar_adapter.py`)
para ejercer `create_event` sin tocar Nextcloud. Se verifica: el PUT construye un ICS
válido y va a la RUTA correcta (`/calendars/<uid>/<calendario>/<uid-evento>.ics`) con el
header de impersonation `AUTHORIZATION-APP-API = b64(uid:app_secret)` (secreto NUNCA en
claro), `Content-Type: text/calendar` e `If-None-Match: *`; que DTSTART lleva la tz del
usuario (TZID + VTIMEZONE); 201/204 ⇒ éxito con href; 403/412 ⇒ `CreatedEvent` de error
(sin excepción cruda); el override de calendario; y el rechazo con uid vacío.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.adapters.nextcloud_calendar_adapter import (
    CalendarError,
    NextcloudCalendarAdapter,
)
from app.domain.calendar import NewCalendarEvent

_SECRET = "s3cr3t-app-secret"
BOGOTA = ZoneInfo("America/Bogota")  # UTC-5, sin DST
_FIXED_UID = "evt-fixed-uid"
_FIXED_DTSTAMP = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _adapter(handler) -> NextcloudCalendarAdapter:
    return NextcloudCalendarAdapter(
        endpoint="https://nc.example.com",
        app_id="gcf_bot",
        app_version="1.2.3",
        app_secret=_SECRET,
        transport=httpx.MockTransport(handler),
        uid_factory=lambda: _FIXED_UID,  # UID determinista ⇒ ruta y cuerpo reproducibles
        now_fn=lambda: _FIXED_DTSTAMP,
    )


def _draft(**overrides) -> NewCalendarEvent:
    base = dict(
        summary="Reunión cliente",
        start=datetime(2026, 7, 1, 9, 0, tzinfo=BOGOTA),
        end=datetime(2026, 7, 1, 10, 0, tzinfo=BOGOTA),
    )
    base.update(overrides)
    return NewCalendarEvent(**base)


@pytest.mark.asyncio
async def test_put_builds_valid_ics_at_correct_route_impersonating():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    adapter = _adapter(handler)

    result = await adapter.create_event("mmazo", _draft())

    assert result.ok is True
    assert result.status == 201
    assert result.uid == _FIXED_UID
    assert result.calendar == "personal"

    request = seen[0]
    # RUTA: PUT a /calendars/<uid>/personal/<uid-evento>.ics (default 'personal').
    assert request.method == "PUT"
    expected_path = f"/remote.php/dav/calendars/mmazo/personal/{_FIXED_UID}.ics"
    assert request.url.path == expected_path
    assert result.href == expected_path
    # Cabeceras de escritura CalDAV.
    assert request.headers["Content-Type"] == "text/calendar; charset=utf-8"
    assert request.headers["If-None-Match"] == "*"

    body = request.content.decode("utf-8")
    assert "BEGIN:VCALENDAR" in body and "END:VCALENDAR" in body
    assert "BEGIN:VEVENT" in body and "END:VEVENT" in body
    assert f"UID:{_FIXED_UID}" in body
    assert "SUMMARY:Reunión cliente" in body
    assert "DTSTAMP:20260701T120000Z" in body

    # Impersonation: el uid viaja embebido en AUTHORIZATION-APP-API (b64), no aparte.
    token = request.headers["AUTHORIZATION-APP-API"]
    assert base64.b64decode(token).decode("utf-8") == f"mmazo:{_SECRET}"
    # El secreto en claro NUNCA viaja fuera de ese token (ni en el cuerpo del ICS).
    assert _SECRET not in body
    for key, value in request.headers.items():
        if key.lower() != "authorization-app-api":
            assert _SECRET not in value


@pytest.mark.asyncio
async def test_dtstart_carries_user_tz_with_vtimezone():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    adapter = _adapter(handler)

    await adapter.create_event("mmazo", _draft())

    body = seen[0].content.decode("utf-8")
    # 09:00 en Bogotá se emite como hora LOCAL anclada a TZID (no como UTC).
    assert "DTSTART;TZID=America/Bogota:20260701T090000" in body
    assert "DTEND;TZID=America/Bogota:20260701T100000" in body
    # Y con su bloque VTIMEZONE (offset -0500 vigente en la fecha del evento).
    assert "BEGIN:VTIMEZONE" in body
    assert "TZID:America/Bogota" in body
    assert "TZOFFSETTO:-0500" in body


@pytest.mark.asyncio
async def test_optional_description_and_location_are_escaped():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    adapter = _adapter(handler)

    await adapter.create_event(
        "mmazo", _draft(description="línea; con, comas", location="Sala A")
    )

    body = seen[0].content.decode("utf-8")
    # RFC 5545 §3.3.11: ';' y ',' se escapan con '\'.
    assert "DESCRIPTION:línea\\; con\\, comas" in body
    assert "LOCATION:Sala A" in body


@pytest.mark.asyncio
async def test_calendar_override_targets_named_calendar():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    adapter = _adapter(handler)

    result = await adapter.create_event("mmazo", _draft(calendar="work"))

    assert result.calendar == "work"
    assert seen[0].url.path == f"/remote.php/dav/calendars/mmazo/work/{_FIXED_UID}.ics"


@pytest.mark.asyncio
async def test_204_no_content_is_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    adapter = _adapter(handler)

    result = await adapter.create_event("mmazo", _draft())

    assert result.ok is True
    assert result.status == 204


@pytest.mark.asyncio
async def test_403_returns_error_result_not_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    adapter = _adapter(handler)

    result = await adapter.create_event("mmazo", _draft())

    # 403 es DATO, no excepción cruda: CreatedEvent de error.
    assert result.ok is False
    assert result.status == 403
    assert result.href is None
    assert "403" in result.error
    lowered = result.error.lower()
    assert "permiso" in lowered or "csrf" in lowered


@pytest.mark.asyncio
async def test_412_precondition_returns_error_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(412)

    adapter = _adapter(handler)

    result = await adapter.create_event("mmazo", _draft())

    assert result.ok is False
    assert result.status == 412
    assert "412" in result.error


@pytest.mark.asyncio
async def test_empty_uid_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201)

    adapter = _adapter(handler)

    with pytest.raises(CalendarError):
        await adapter.create_event("", _draft())

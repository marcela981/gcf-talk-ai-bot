"""Unit tests for app.adapters.nextcloud_calendar_adapter (ADR-016), sin red.

Se inyecta un `httpx.MockTransport` (mismo patrón que el loader de Supabase) para
ejercer el adapter sin tocar Nextcloud. Se verifica: la secuencia PROPFIND→REPORT,
que impersona al usuario vía `AUTHORIZATION-APP-API = b64(uid:app_secret)`, que el
secreto en claro NUNCA viaja fuera de ese token, el ordenado de eventos, la
degradación cuando un calendario falla, y el error en PROPFIND no-207.
"""
from __future__ import annotations

import base64
from datetime import date

import httpx
import pytest

from app.adapters.nextcloud_calendar_adapter import (
    CalendarError,
    NextcloudCalendarAdapter,
)
from app.domain.calendar import DateRange

_SECRET = "s3cr3t-app-secret"

_PROPFIND_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/remote.php/dav/calendars/mmazo/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/calendars/mmazo/personal/</d:href>
    <d:propstat><d:prop>
      <d:resourcetype><d:collection/><cal:calendar/></d:resourcetype>
    </d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/calendars/mmazo/work/</d:href>
    <d:propstat><d:prop>
      <d:resourcetype><d:collection/><cal:calendar/></d:resourcetype>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>"""


def _report_xml(summary: str, hhmm: str) -> str:
    ical = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\n"
        f"SUMMARY:{summary}\n"
        f"DTSTART:20260630T{hhmm}00Z\n"
        "END:VEVENT\n"
        "END:VCALENDAR"
    )
    return (
        '<?xml version="1.0"?>'
        '<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
        "<d:response><d:href>/x.ics</d:href><d:propstat><d:prop>"
        f"<cal:calendar-data>{ical}</cal:calendar-data>"
        "</d:prop></d:propstat></d:response>"
        "</d:multistatus>"
    )


def _adapter(handler) -> NextcloudCalendarAdapter:
    return NextcloudCalendarAdapter(
        endpoint="https://nc.example.com",
        app_id="gcf_bot",
        app_version="1.2.3",
        app_secret=_SECRET,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_propfind_then_report_impersonating_user():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_PROPFIND_XML)
        if request.method == "REPORT":
            # Distinto evento por calendario para probar el ordenado por inicio.
            if request.url.path.endswith("/work/"):
                return httpx.Response(207, text=_report_xml("Standup", "08"))
            return httpx.Response(207, text=_report_xml("Cliente", "09"))
        return httpx.Response(405)

    adapter = _adapter(handler)

    events = await adapter.list_events("mmazo", DateRange.for_day(date(2026, 6, 30)))

    # PROPFIND a la home del usuario, luego un REPORT por calendario descubierto.
    assert seen[0].method == "PROPFIND"
    assert seen[0].url.path == "/remote.php/dav/calendars/mmazo/"
    report_paths = [r.url.path for r in seen if r.method == "REPORT"]
    assert report_paths == [
        "/remote.php/dav/calendars/mmazo/personal/",
        "/remote.php/dav/calendars/mmazo/work/",
    ]
    # Eventos de ambos calendarios, ordenados por hora de inicio (08:00 antes que 09:00).
    assert [e.summary for e in events] == ["Standup", "Cliente"]
    # El REPORT lleva el filtro time-range.
    report_req = next(r for r in seen if r.method == "REPORT")
    assert b"time-range" in report_req.content


@pytest.mark.asyncio
async def test_impersonation_header_encodes_uid_and_secret():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_PROPFIND_XML)
        return httpx.Response(207, text=_report_xml("X", "10"))

    adapter = _adapter(handler)
    await adapter.list_events("mmazo", DateRange.for_day(date(2026, 6, 30)))

    token = seen[0].headers["AUTHORIZATION-APP-API"]
    assert base64.b64decode(token).decode("utf-8") == f"mmazo:{_SECRET}"
    # El secreto en claro NUNCA viaja fuera del token base64.
    for request in seen:
        for key, value in request.headers.items():
            if key.lower() != "authorization-app-api":
                assert _SECRET not in value


@pytest.mark.asyncio
async def test_failing_calendar_is_skipped_not_fatal():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_PROPFIND_XML)
        if request.url.path.endswith("/personal/"):
            return httpx.Response(500, text="boom")  # un calendario falla
        return httpx.Response(207, text=_report_xml("Sobrevive", "11"))

    adapter = _adapter(handler)

    events = await adapter.list_events("mmazo", DateRange.for_day(date(2026, 6, 30)))

    # El calendario que respondió 207 sigue aportando su evento.
    assert [e.summary for e in events] == ["Sobrevive"]


@pytest.mark.asyncio
async def test_propfind_non_207_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    adapter = _adapter(handler)

    with pytest.raises(CalendarError, match="PROPFIND"):
        await adapter.list_events("mmazo", DateRange.for_day(date(2026, 6, 30)))


@pytest.mark.asyncio
async def test_events_outside_the_day_are_filtered_out():
    # El servidor puede sobre-devolver en los bordes; el filtro aware-vs-aware del
    # adapter (DateRange.contains) descarta lo que no cae en el día consultado.
    def _report_on(date_compact: str, summary: str) -> str:
        ical = (
            "BEGIN:VEVENT\n"
            f"SUMMARY:{summary}\n"
            f"DTSTART:{date_compact}T100000Z\n"
            "END:VEVENT"
        )
        return (
            '<?xml version="1.0"?>'
            '<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
            "<d:response><d:href>/x.ics</d:href><d:propstat><d:prop>"
            f"<cal:calendar-data>{ical}</cal:calendar-data>"
            "</d:prop></d:propstat></d:response></d:multistatus>"
        )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_PROPFIND_XML)
        if request.url.path.endswith("/personal/"):
            return httpx.Response(207, text=_report_on("20260630", "Hoy"))
        return httpx.Response(207, text=_report_on("20260701", "Mañana"))

    adapter = _adapter(handler)

    events = await adapter.list_events("mmazo", DateRange.for_day(date(2026, 6, 30)))

    assert [e.summary for e in events] == ["Hoy"]


def test_missing_credentials_rejected_at_construction():
    with pytest.raises(CalendarError):
        NextcloudCalendarAdapter(
            endpoint="", app_id="x", app_version="1", app_secret="y"
        )

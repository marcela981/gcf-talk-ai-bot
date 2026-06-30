"""Unit tests for app.adapters.nextcloud_calendar_adapter (ADR-016 / Bloque 2.1), sin red.

Se inyecta un `httpx.MockTransport` (mismo patrón que el loader de Supabase) para
ejercer el adapter sin tocar Nextcloud. Se verifica: la secuencia PROPFIND→REPORT,
que impersona al usuario vía `AUTHORIZATION-APP-API = b64(uid:app_secret)`, que el
secreto en claro NUNCA viaja fuera de ese token, el REPORT con time-range + expansión
server-side (`<c:expand>`), las ocurrencias recurrentes en un rango, el dedup, el
warning cuando el servidor NO expande, el ordenado, la degradación cuando un
calendario falla, y el error en PROPFIND no-207.
"""
from __future__ import annotations

import base64
import logging
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

# PROPFIND con un único calendario (para tests que solo necesitan un REPORT).
_PROPFIND_ONE = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/remote.php/dav/calendars/mmazo/personal/</d:href>
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
    # El REPORT lleva el filtro time-range Y la expansión server-side.
    report_req = next(r for r in seen if r.method == "REPORT")
    assert b"time-range" in report_req.content
    assert b"expand" in report_req.content


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


def _multistatus(*calendar_data_bodies: str) -> str:
    responses = "".join(
        "<d:response><d:href>/x.ics</d:href><d:propstat><d:prop>"
        f"<cal:calendar-data>{body}</cal:calendar-data>"
        "</d:prop></d:propstat></d:response>"
        for body in calendar_data_bodies
    )
    return (
        '<?xml version="1.0"?>'
        '<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
        f"{responses}</d:multistatus>"
    )


@pytest.mark.asyncio
async def test_recurring_occurrences_in_a_range_are_returned():
    # El servidor expande la serie semanal: una instancia por ocurrencia en el rango.
    inst1 = (
        "BEGIN:VEVENT\nUID:wk@x\nSUMMARY:Semanal\n"
        "DTSTART:20260701T140000Z\nRECURRENCE-ID:20260701T140000Z\nEND:VEVENT"
    )
    inst2 = (
        "BEGIN:VEVENT\nUID:wk@x\nSUMMARY:Semanal\n"
        "DTSTART:20260708T140000Z\nRECURRENCE-ID:20260708T140000Z\nEND:VEVENT"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_PROPFIND_XML)
        if request.url.path.endswith("/personal/"):
            return httpx.Response(207, text=_multistatus(inst1, inst2))
        return httpx.Response(207, text=_multistatus())  # work: vacío

    adapter = _adapter(handler)

    events = await adapter.list_events(
        "mmazo", DateRange.for_range(date(2026, 6, 30), date(2026, 7, 13))
    )

    # Ambas ocurrencias futuras aparecen (lo que el bug de smoke no devolvía).
    assert [e.start.isoformat() for e in events] == [
        "2026-07-01T14:00:00+00:00",
        "2026-07-08T14:00:00+00:00",
    ]


@pytest.mark.asyncio
async def test_instance_and_override_are_deduped_across_the_report():
    inst = (
        "BEGIN:VEVENT\nUID:wk@x\nSUMMARY:Semanal\n"
        "DTSTART:20260630T140000Z\nRECURRENCE-ID:20260630T140000Z\nEND:VEVENT"
    )
    override = (
        "BEGIN:VEVENT\nUID:wk@x\nSUMMARY:Semanal movida\n"
        "DTSTART:20260630T150000Z\nRECURRENCE-ID:20260630T140000Z\nEND:VEVENT"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_PROPFIND_ONE)
        return httpx.Response(207, text=_multistatus(inst, override))

    adapter = _adapter(handler)

    events = await adapter.list_events("mmazo", DateRange.for_day(date(2026, 6, 30)))

    # La misma ocurrencia (UID+RECURRENCE-ID) no se cuenta dos veces.
    assert len(events) == 1


@pytest.mark.asyncio
async def test_warns_when_server_does_not_expand(caplog):
    # El servidor ignora <c:expand> y devuelve el maestro con RRULE dentro del rango.
    master = (
        "BEGIN:VEVENT\nUID:wk@x\nSUMMARY:Semanal\n"
        "DTSTART:20260630T140000Z\nRRULE:FREQ=WEEKLY\nEND:VEVENT"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=_PROPFIND_ONE)
        return httpx.Response(207, text=_multistatus(master))

    adapter = _adapter(handler)

    with caplog.at_level(logging.WARNING):
        events = await adapter.list_events("mmazo", DateRange.for_day(date(2026, 6, 30)))

    assert any("no expandió las recurrencias" in r.message for r in caplog.records)
    # No se expande en cliente: se devuelve lo disponible (el maestro en rango).
    assert [e.recurring for e in events] == [True]


def test_missing_credentials_rejected_at_construction():
    with pytest.raises(CalendarError):
        NextcloudCalendarAdapter(
            endpoint="", app_id="x", app_version="1", app_secret="y"
        )

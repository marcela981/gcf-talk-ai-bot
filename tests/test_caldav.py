"""Unit tests for app.domain.caldav (ADR-016): parseo CalDAV/iCal puro, sin red.

Cubre lo que el adapter delega al dominio: descubrir los hrefs de calendarios desde
un multistatus PROPFIND (portado del spike), construir el REPORT calendar-query con
el time-range correcto, y parsear los VEVENT (UTC, todo-el-día, line-folding y
escapes) a `CalendarEvent`.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.domain.caldav import (
    build_calendar_query,
    parse_calendar_hrefs,
    parse_events,
)
from app.domain.calendar import DateRange

_PROPFIND_MULTISTATUS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/remote.php/dav/calendars/mmazo/</d:href>
    <d:propstat><d:prop>
      <d:resourcetype><d:collection/></d:resourcetype>
    </d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/calendars/mmazo/personal/</d:href>
    <d:propstat><d:prop>
      <d:resourcetype><d:collection/><cal:calendar/></d:resourcetype>
      <d:displayname>Personal</d:displayname>
    </d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/calendars/mmazo/work/</d:href>
    <d:propstat><d:prop>
      <d:resourcetype><d:collection/><cal:calendar/></d:resourcetype>
      <d:displayname>Work</d:displayname>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>"""


def test_parse_calendar_hrefs_skips_home_and_non_calendars():
    hrefs = parse_calendar_hrefs(_PROPFIND_MULTISTATUS)

    # La home (sin <cal:calendar/>) se omite; solo quedan las colecciones-calendario.
    assert hrefs == [
        "/remote.php/dav/calendars/mmazo/personal/",
        "/remote.php/dav/calendars/mmazo/work/",
    ]


def test_build_calendar_query_has_utc_time_range():
    body = build_calendar_query(DateRange.for_day(date(2026, 6, 30)))

    assert 'start="20260630T000000Z"' in body
    assert 'end="20260701T000000Z"' in body
    assert "VEVENT" in body and "calendar-data" in body


def _report_with(ical_body: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
        "<d:response><d:href>/x.ics</d:href><d:propstat><d:prop>"
        f"<cal:calendar-data>{ical_body}</cal:calendar-data>"
        "</d:prop></d:propstat></d:response>"
        "</d:multistatus>"
    )


def test_parse_events_utc_timed_event():
    ical = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\n"
        "SUMMARY:Reunión con cliente\n"
        "DTSTART:20260630T090000Z\n"
        "DTEND:20260630T100000Z\n"
        "END:VEVENT\n"
        "END:VCALENDAR"
    )
    events = parse_events(_report_with(ical), calendar="personal")

    assert len(events) == 1
    ev = events[0]
    assert ev.summary == "Reunión con cliente"
    assert ev.start == datetime(2026, 6, 30, 9, 0, tzinfo=timezone.utc)
    assert ev.end == datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    assert ev.all_day is False
    assert ev.calendar == "personal"


def test_parse_events_all_day_event():
    ical = (
        "BEGIN:VEVENT\n"
        "SUMMARY:Festivo\n"
        "DTSTART;VALUE=DATE:20260630\n"
        "DTEND;VALUE=DATE:20260701\n"
        "END:VEVENT"
    )
    (ev,) = parse_events(_report_with(ical))

    assert ev.all_day is True
    assert ev.start == datetime(2026, 6, 30, 0, 0, tzinfo=timezone.utc)


def test_parse_events_unfolds_lines_and_unescapes():
    # SUMMARY plegada a mitad de palabra (RFC5545: unfold = quitar CRLF + 1 espacio
    # y concatenar directo) y con escape de coma.
    ical = (
        "BEGIN:VEVENT\n"
        "SUMMARY:Almuerzo\\, plann\n"
        " ing y retro\n"
        "DTSTART:20260630T120000Z\n"
        "END:VEVENT"
    )
    (ev,) = parse_events(_report_with(ical))

    assert ev.summary == "Almuerzo, planning y retro"
    assert ev.end is None  # sin DTEND


def test_parse_events_handles_multiple_vevents():
    ical = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\nSUMMARY:Uno\nDTSTART:20260630T080000Z\nEND:VEVENT\n"
        "BEGIN:VEVENT\nSUMMARY:Dos\nDTSTART:20260630T090000Z\nEND:VEVENT\n"
        "END:VCALENDAR"
    )
    events = parse_events(_report_with(ical))

    assert [e.summary for e in events] == ["Uno", "Dos"]


def test_parse_events_empty_when_no_calendar_data():
    xml = (
        '<d:multistatus xmlns:d="DAV:" '
        'xmlns:cal="urn:ietf:params:xml:ns:caldav"></d:multistatus>'
    )
    assert parse_events(xml) == []

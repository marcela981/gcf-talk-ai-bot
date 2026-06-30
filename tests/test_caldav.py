"""Unit tests for app.domain.caldav (ADR-016 / Bloque 2.1): parseo CalDAV/iCal puro.

Sin red. Cubre lo que el adapter delega al dominio: descubrir hrefs de calendarios
(PROPFIND, portado del spike), construir el REPORT calendar-query con el time-range
UTC correcto, y parsear los VEVENT a `CalendarEvent` SIEMPRE en UTC-aware. El foco
nuevo es la **zona horaria**: el "día" se enmarca en la zona del usuario y los
DTSTART (Z / TZID / VALUE=DATE / flotante) se normalizan a UTC; la pertenencia al
día se compara aware-vs-aware (DateRange.contains), cubriendo el cruce de día por el
offset -5 de America/Bogota (el bug confirmado en smoke).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.domain.caldav import (
    build_calendar_query,
    parse_calendar_hrefs,
    parse_events,
)
from app.domain.calendar import DateRange

BOGOTA = ZoneInfo("America/Bogota")  # UTC-5, sin DST

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


def _report_with(ical_body: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
        "<d:response><d:href>/x.ics</d:href><d:propstat><d:prop>"
        f"<cal:calendar-data>{ical_body}</cal:calendar-data>"
        "</d:prop></d:propstat></d:response>"
        "</d:multistatus>"
    )


def _one_event(ical_body: str, *, tz=BOGOTA):
    (event,) = parse_events(_report_with(ical_body), tz=tz)
    return event


# --- Descubrimiento y construcción de la query --------------------------------


def test_parse_calendar_hrefs_skips_home_and_non_calendars():
    hrefs = parse_calendar_hrefs(_PROPFIND_MULTISTATUS)

    # La home (sin <cal:calendar/>) se omite; solo quedan las colecciones-calendario.
    assert hrefs == [
        "/remote.php/dav/calendars/mmazo/personal/",
        "/remote.php/dav/calendars/mmazo/work/",
    ]


def test_build_calendar_query_time_range_is_utc_framed_in_user_zone():
    # El día local de Bogotá [00:00, 24:00) -5 son [05:00Z, 05:00Z del día siguiente).
    body = build_calendar_query(DateRange.for_day(date(2026, 6, 30), tz=BOGOTA))

    assert 'start="20260630T050000Z"' in body
    assert 'end="20260701T050000Z"' in body
    assert "VEVENT" in body and "calendar-data" in body


def test_build_calendar_query_requests_server_side_expand():
    # Expansión server-side de recurrencias: <c:expand> con el mismo rango UTC.
    body = build_calendar_query(DateRange.for_day(date(2026, 6, 30), tz=BOGOTA))

    assert '<c:expand start="20260630T050000Z" end="20260701T050000Z"/>' in body


def test_build_calendar_query_spans_a_multi_day_range():
    body = build_calendar_query(
        DateRange.for_range(date(2026, 6, 30), date(2026, 7, 13), tz=BOGOTA)
    )

    # [30 jun 00:00, 14 jul 00:00) local -5 → [05:00Z 30 jun, 05:00Z 14 jul).
    assert 'start="20260630T050000Z"' in body
    assert 'end="20260714T050000Z"' in body


# --- Normalización de DTSTART/DTEND a UTC -------------------------------------


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
    events = parse_events(_report_with(ical), tz=BOGOTA, calendar="personal")

    assert len(events) == 1
    ev = events[0]
    assert ev.summary == "Reunión con cliente"
    # Sufijo Z → UTC, independientemente de la zona del usuario.
    assert ev.start == datetime(2026, 6, 30, 9, 0, tzinfo=timezone.utc)
    assert ev.end == datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    assert ev.all_day is False
    assert ev.calendar == "personal"


def test_parse_events_tzid_is_localized_then_converted_to_utc():
    ical = (
        "BEGIN:VEVENT\n"
        "SUMMARY:Cena\n"
        "DTSTART;TZID=America/Bogota:20260630T230000\n"
        "DTEND;TZID=America/Bogota:20260701T000000\n"
        "END:VEVENT"
    )
    ev = _one_event(ical, tz=timezone.utc)  # la zona del usuario no se usa: hay TZID

    # 23:00 en Bogotá (UTC-5) = 04:00 UTC del día siguiente.
    assert ev.start == datetime(2026, 7, 1, 4, 0, tzinfo=timezone.utc)
    assert ev.end == datetime(2026, 7, 1, 5, 0, tzinfo=timezone.utc)


def test_parse_events_floating_assumes_user_zone():
    ical = "BEGIN:VEVENT\nSUMMARY:Flotante\nDTSTART:20260630T230000\nEND:VEVENT"

    ev = _one_event(ical, tz=BOGOTA)  # sin Z ni TZID → zona del usuario

    assert ev.start == datetime(2026, 7, 1, 4, 0, tzinfo=timezone.utc)


def test_parse_events_unknown_tzid_falls_back_to_user_zone():
    ical = (
        "BEGIN:VEVENT\nSUMMARY:Zona rara\n"
        "DTSTART;TZID=Mars/Olympus:20260630T230000\nEND:VEVENT"
    )
    ev = _one_event(ical, tz=BOGOTA)  # TZID inválido degrada a la zona del usuario

    assert ev.start == datetime(2026, 7, 1, 4, 0, tzinfo=timezone.utc)


def test_parse_events_all_day_is_local_full_day():
    ical = (
        "BEGIN:VEVENT\nSUMMARY:Festivo\n"
        "DTSTART;VALUE=DATE:20260630\nDTEND;VALUE=DATE:20260701\nEND:VEVENT"
    )
    ev = _one_event(ical, tz=BOGOTA)

    assert ev.all_day is True
    # Medianoche local de Bogotá = 05:00 UTC.
    assert ev.start == datetime(2026, 6, 30, 5, 0, tzinfo=timezone.utc)


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
    ev = _one_event(ical)

    assert ev.summary == "Almuerzo, planning y retro"
    assert ev.end is None  # sin DTEND


def test_parse_events_handles_multiple_vevents():
    ical = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\nSUMMARY:Uno\nDTSTART:20260630T080000Z\nEND:VEVENT\n"
        "BEGIN:VEVENT\nSUMMARY:Dos\nDTSTART:20260630T090000Z\nEND:VEVENT\n"
        "END:VCALENDAR"
    )
    events = parse_events(_report_with(ical), tz=BOGOTA)

    assert [e.summary for e in events] == ["Uno", "Dos"]


def test_parse_events_empty_when_no_calendar_data():
    xml = (
        '<d:multistatus xmlns:d="DAV:" '
        'xmlns:cal="urn:ietf:params:xml:ns:caldav"></d:multistatus>'
    )
    assert parse_events(xml, tz=BOGOTA) == []


# --- Recurrencias: expansión, dedup y detección de no-expansión ---------------


def test_expanded_occurrences_same_uid_are_all_kept():
    # Lo que devuelve un servidor que SÍ expande: una instancia por ocurrencia,
    # mismo UID, RECURRENCE-ID distinto, sin RRULE.
    ical = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\nUID:wk@x\nSUMMARY:Semanal\n"
        "DTSTART:20260701T140000Z\nRECURRENCE-ID:20260701T140000Z\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:wk@x\nSUMMARY:Semanal\n"
        "DTSTART:20260708T140000Z\nRECURRENCE-ID:20260708T140000Z\nEND:VEVENT\n"
        "END:VCALENDAR"
    )
    events = parse_events(_report_with(ical), tz=BOGOTA)

    assert [e.start for e in events] == [
        datetime(2026, 7, 1, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 8, 14, 0, tzinfo=timezone.utc),
    ]
    assert all(e.recurring is False for e in events)  # instancias, no maestro


def test_dedup_collapses_instance_and_override_same_recurrence_id():
    # Misma ocurrencia (UID + RECURRENCE-ID) llega dos veces (instancia + override):
    # se conserva una sola (evita el doble-conteo visto en smoke).
    ical = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\nUID:wk@x\nSUMMARY:Semanal\n"
        "DTSTART:20260701T140000Z\nRECURRENCE-ID:20260701T140000Z\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:wk@x\nSUMMARY:Semanal (movida)\n"
        "DTSTART:20260701T150000Z\nRECURRENCE-ID:20260701T140000Z\nEND:VEVENT\n"
        "END:VCALENDAR"
    )
    events = parse_events(_report_with(ical), tz=BOGOTA)

    assert len(events) == 1


def test_same_uid_without_recurrence_id_not_collapsed_by_distinct_start():
    # Sin RECURRENCE-ID, se distingue por el inicio concreto: no se colapsan.
    ical = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\nUID:wk@x\nSUMMARY:A\nDTSTART:20260701T140000Z\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:wk@x\nSUMMARY:B\nDTSTART:20260708T140000Z\nEND:VEVENT\n"
        "END:VCALENDAR"
    )
    events = parse_events(_report_with(ical), tz=BOGOTA)

    assert [e.summary for e in events] == ["A", "B"]


def test_master_with_rrule_is_flagged_recurring():
    # Lo que devuelve un servidor que NO expande: el maestro con su RRULE intacta.
    ical = (
        "BEGIN:VEVENT\nUID:wk@x\nSUMMARY:Semanal maestro\n"
        "DTSTART:20260101T140000Z\nRRULE:FREQ=WEEKLY\nEND:VEVENT"
    )
    ev = _one_event(ical, tz=BOGOTA)

    assert ev.recurring is True


# --- Pertenencia al "día" del usuario (aware vs aware, cruce por offset -5) ----


def test_event_at_2300_local_belongs_to_today():
    # EL BUG VIEJO: con fronteras UTC, 23:00 local (04:00Z del día siguiente) se caía
    # fuera de "hoy". Con el día enmarcado en Bogotá, pertenece.
    today = DateRange.for_day(date(2026, 6, 30), tz=BOGOTA)
    ev = _one_event(
        "BEGIN:VEVENT\nSUMMARY:Tarde\n"
        "DTSTART;TZID=America/Bogota:20260630T230000\nEND:VEVENT",
        tz=BOGOTA,
    )

    assert ev.start == datetime(2026, 7, 1, 4, 0, tzinfo=timezone.utc)
    assert today.contains(ev.start) is True


def test_event_tomorrow_morning_not_in_today():
    today = DateRange.for_day(date(2026, 6, 30), tz=BOGOTA)
    ev = _one_event(
        "BEGIN:VEVENT\nSUMMARY:Mañana\n"
        "DTSTART;TZID=America/Bogota:20260701T100000\nEND:VEVENT",
        tz=BOGOTA,
    )

    assert today.contains(ev.start) is False


def test_local_midnight_is_inclusive_start_of_today():
    today = DateRange.for_day(date(2026, 6, 30), tz=BOGOTA)
    at_midnight = _one_event(
        "BEGIN:VEVENT\nSUMMARY:Medianoche\n"
        "DTSTART;TZID=America/Bogota:20260630T000000\nEND:VEVENT",
        tz=BOGOTA,
    )
    before = _one_event(
        "BEGIN:VEVENT\nSUMMARY:Ayer tarde\n"
        "DTSTART;TZID=America/Bogota:20260629T235900\nEND:VEVENT",
        tz=BOGOTA,
    )

    assert today.contains(at_midnight.start) is True  # [start inclusivo
    assert today.contains(before.start) is False

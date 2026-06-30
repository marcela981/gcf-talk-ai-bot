"""Parseo y construcción de cuerpos CalDAV/iCalendar (dominio puro, stdlib).

Portado de la lógica que el spike de ADR-016 validó offline (``_parse_calendars``),
extendido para **construir** el REPORT ``calendar-query`` con filtro ``time-range``
y para **parsear** los ``VEVENT`` de la respuesta a :class:`CalendarEvent`. Solo usa
la stdlib (``xml``/``datetime``), así que se testea sin red — el adapter de Nextcloud
(infra) hace el I/O y **delega aquí** la transformación de formato (mismo patrón que
la reconciliación tiktoken↔capas de ARCHITECTURE §3).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from app.domain.calendar import CalendarEvent, DateRange

_DAV_NS = "{DAV:}"
_CALDAV_NS = "{urn:ietf:params:xml:ns:caldav}"

# PROPFIND (Depth: 1) para listar las colecciones-calendario del usuario.
PROPFIND_CALENDARS_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
    "<d:prop><d:resourcetype/><d:displayname/></d:prop>"
    "</d:propfind>"
)

_ICAL_DT_UTC = "%Y%m%dT%H%M%SZ"
_ICAL_DT_LOCAL = "%Y%m%dT%H%M%S"
_ICAL_DATE = "%Y%m%d"


def _ical_utc(dt: datetime) -> str:
    """Formatea un datetime como instante UTC compacto (``YYYYMMDDTHHMMSSZ``)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(_ICAL_DT_UTC)


def build_calendar_query(date_range: DateRange) -> str:
    """REPORT ``calendar-query`` que pide los VEVENT en ``[start, end)`` con su data."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        "<d:prop><d:getetag/><c:calendar-data/></d:prop>"
        '<c:filter><c:comp-filter name="VCALENDAR">'
        '<c:comp-filter name="VEVENT">'
        f'<c:time-range start="{_ical_utc(date_range.start)}"'
        f' end="{_ical_utc(date_range.end)}"/>'
        "</c:comp-filter></c:comp-filter></c:filter>"
        "</c:calendar-query>"
    )


def parse_calendar_hrefs(multistatus_xml: str) -> list[str]:
    """hrefs de las colecciones que SON calendarios (``resourcetype`` con ``<cal:calendar/>``).

    La home y las colecciones no-calendario se omiten. Portado de ``_parse_calendars``
    del spike (validado offline), devolviendo solo el href para enrutar el REPORT.
    """
    root = ET.fromstring(multistatus_xml)
    hrefs: list[str] = []
    for response in root.findall(f"{_DAV_NS}response"):
        rtype = response.find(f".//{_DAV_NS}resourcetype")
        is_calendar = (
            rtype is not None and rtype.find(f"{_CALDAV_NS}calendar") is not None
        )
        if not is_calendar:
            continue
        href_el = response.find(f"{_DAV_NS}href")
        if href_el is not None and href_el.text:
            hrefs.append(href_el.text.strip())
    return hrefs


def parse_events(
    multistatus_xml: str, *, calendar: str | None = None
) -> list[CalendarEvent]:
    """Extrae y normaliza los VEVENT de un multistatus ``calendar-query``."""
    root = ET.fromstring(multistatus_xml)
    events: list[CalendarEvent] = []
    for data_el in root.iter(f"{_CALDAV_NS}calendar-data"):
        if data_el.text:
            events.extend(_parse_vevents(data_el.text, calendar=calendar))
    return events


def _unfold(ical_text: str) -> list[str]:
    """Deshace el folding RFC5545: una línea que empieza con espacio/tab continúa la previa."""
    raw = ical_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    for line in raw:
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _unescape(value: str) -> str:
    """Desescapa el texto iCalendar (``\\n``, ``\\,``, ``\\;``, ``\\\\``)."""
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in ("n", "N") else nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out).strip()


def _parse_ical_datetime(name: str, value: str) -> tuple[datetime, bool] | None:
    """Parsea un valor DTSTART/DTEND → ``(datetime, all_day)`` o ``None`` si no encaja.

    Soporta ``...Z`` (UTC, aware), ``VALUE=DATE`` / ``YYYYMMDD`` (todo-el-día,
    medianoche UTC) y datetimes locales/flotantes o con ``TZID`` (naive: BLOQUE 2 no
    convierte zona — no hay base de zonas horarias cableada).
    """
    value = value.strip()
    is_date = "VALUE=DATE" in name.upper() and "DATE-TIME" not in name.upper()
    try:
        if is_date or (len(value) == 8 and value.isdigit()):
            day = datetime.strptime(value, _ICAL_DATE).replace(tzinfo=timezone.utc)
            return (day, True)
        if value.endswith("Z"):
            return (
                datetime.strptime(value, _ICAL_DT_UTC).replace(tzinfo=timezone.utc),
                False,
            )
        return (datetime.strptime(value, _ICAL_DT_LOCAL), False)
    except ValueError:
        return None


def _parse_vevents(ical_text: str, *, calendar: str | None) -> list[CalendarEvent]:
    """Recorre el VCALENDAR y emite un CalendarEvent por cada VEVENT con DTSTART válido."""
    events: list[CalendarEvent] = []
    in_event = False
    summary = ""
    dtstart: tuple[datetime, bool] | None = None
    dtend: tuple[datetime, bool] | None = None

    for line in _unfold(ical_text):
        stripped = line.strip()
        if stripped == "BEGIN:VEVENT":
            in_event, summary, dtstart, dtend = True, "", None, None
            continue
        if stripped == "END:VEVENT":
            if dtstart is not None:
                start_dt, all_day = dtstart
                events.append(
                    CalendarEvent(
                        summary=summary or "(sin título)",
                        start=start_dt,
                        end=dtend[0] if dtend is not None else None,
                        all_day=all_day,
                        calendar=calendar,
                    )
                )
            in_event = False
            continue
        if not in_event:
            continue

        name, sep, value = line.partition(":")
        if not sep:
            continue
        prop = name.split(";", 1)[0].upper()
        if prop == "SUMMARY":
            summary = _unescape(value)
        elif prop == "DTSTART":
            dtstart = _parse_ical_datetime(name, value)
        elif prop == "DTEND":
            dtend = _parse_ical_datetime(name, value)

    return events

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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo

from app.domain.calendar import (
    CalendarEvent,
    DateRange,
    NewCalendarEvent,
    to_zoneinfo,
)


@dataclass(frozen=True)
class _RawVEvent:
    """VEVENT parseado + claves de identidad para deduplicar (uso interno)."""

    event: CalendarEvent
    uid: str | None
    recurrence_id: str | None

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
    """REPORT ``calendar-query`` con ``time-range`` + expansión SERVER-SIDE de recurrencias.

    El ``<c:expand>`` (RFC 4791 §9.6.5) pide al servidor que **genere las ocurrencias
    concretas** de los eventos recurrentes dentro de ``[start, end)`` y devuelva cada
    una como un VEVENT instancia (sin ``RRULE``), en vez del único VEVENT maestro
    (cuyo ``DTSTART`` original suele caer fuera del rango). Así las consultas a fechas
    futuras ven las repeticiones. NO se implementa motor RRULE en cliente: si el
    servidor ignora ``<c:expand>``, el adapter lo detecta y avisa (deuda explícita).
    """
    start = _ical_utc(date_range.start)
    end = _ical_utc(date_range.end)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        "<d:prop><c:calendar-data>"
        f'<c:expand start="{start}" end="{end}"/>'
        "</c:calendar-data></d:prop>"
        '<c:filter><c:comp-filter name="VCALENDAR">'
        '<c:comp-filter name="VEVENT">'
        f'<c:time-range start="{start}" end="{end}"/>'
        "</c:comp-filter></c:comp-filter></c:filter>"
        "</c:calendar-query>"
    )


_DEFAULT_PRODID = "-//GCF//Talk AI Bot//ES"
_MAX_LINE_OCTETS = 75  # RFC 5545 §3.1: content lines se pliegan a <=75 octetos.


def build_vevent_ics(
    event: NewCalendarEvent,
    *,
    uid: str,
    dtstamp: datetime,
    prodid: str = _DEFAULT_PRODID,
) -> str:
    """Construye un VCALENDAR/VEVENT iCalendar válido para el PUT de creación (Bloque 2.2).

    ``uid`` (nombre del recurso ``.ics``) y ``dtstamp`` (marca de creación) se inyectan
    desde el adapter para que esta función sea **pura y determinista** (testeable offline).

    ZONA HORARIA — DTSTART/DTEND se emiten con la hora **local del usuario** anclada a
    ``TZID`` **más** un bloque ``VTIMEZONE`` (RFC 5545 §3.6.5), de modo que el instante
    quede definido sin depender de que el servidor conozca la zona. El offset del
    ``VTIMEZONE`` se toma del que la ``tzinfo`` (``ZoneInfo``) reporta **en el instante
    del evento**: es un ``VTIMEZONE`` de **offset fijo por evento**, correcto para el
    instante creado y estructuralmente conforme; NO modela las transiciones DST del huso
    (simplificación deliberada — un evento puntual no cruza un cambio de hora). Si la
    ``tzinfo`` no expone un nombre IANA (``key``) o es UTC, se cae a la forma UTC ``...Z``
    (sin ``VTIMEZONE``), igual de válida.
    """
    start, end = event.start, event.end
    tzid = getattr(start.tzinfo, "key", None)

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{prodid}", "CALSCALE:GREGORIAN"]
    if tzid and tzid.upper() != "UTC":
        lines.extend(_vtimezone_lines(tzid, start))
        dtstart = f"DTSTART;TZID={tzid}:{start.strftime(_ICAL_DT_LOCAL)}"
        dtend = f"DTEND;TZID={tzid}:{end.strftime(_ICAL_DT_LOCAL)}"
    else:
        dtstart = f"DTSTART:{_ical_utc(start)}"
        dtend = f"DTEND:{_ical_utc(end)}"

    lines.extend(
        [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{_ical_utc(dtstamp)}",
            dtstart,
            dtend,
            f"SUMMARY:{_escape(event.summary)}",
        ]
    )
    if event.description:
        lines.append(f"DESCRIPTION:{_escape(event.description)}")
    if event.location:
        lines.append(f"LOCATION:{_escape(event.location)}")
    lines.extend(["END:VEVENT", "END:VCALENDAR"])

    return "".join(f"{_fold(line)}\r\n" for line in lines)


def _vtimezone_lines(tzid: str, reference: datetime) -> list[str]:
    """VTIMEZONE de offset fijo con el offset vigente en ``reference`` (ver `build_vevent_ics`)."""
    offset = _format_utc_offset(reference.utcoffset() or timedelta(0))
    tzname = reference.tzname() or offset
    return [
        "BEGIN:VTIMEZONE",
        f"TZID:{tzid}",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        f"TZOFFSETFROM:{offset}",
        f"TZOFFSETTO:{offset}",
        f"TZNAME:{tzname}",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]


def _format_utc_offset(delta: timedelta) -> str:
    """Formatea un offset UTC como ``±HHMM`` (p. ej. ``-0500`` para Bogotá)."""
    total = int(delta.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}{(total % 3600) // 60:02d}"


def _escape(text: str) -> str:
    """Escapa texto iCalendar (RFC 5545 §3.3.11): ``\\``, ``,``, ``;`` y saltos de línea."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return (
        normalized.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _fold(line: str) -> str:
    """Pliega una línea a <=75 octetos (RFC 5545 §3.1) sin partir un carácter multibyte.

    Las líneas de continuación empiezan con un espacio (que cuenta para el límite), así
    que tras la primera el corte es a 74 octetos. Líneas cortas pasan intactas.
    """
    raw = line.encode("utf-8")
    if len(raw) <= _MAX_LINE_OCTETS:
        return line
    parts: list[bytes] = []
    start, limit = 0, _MAX_LINE_OCTETS
    while start < len(raw):
        end = min(start + limit, len(raw))
        while end < len(raw) and (raw[end] & 0xC0) == 0x80:  # no partir UTF-8 multibyte
            end -= 1
        parts.append(raw[start:end])
        start, limit = end, _MAX_LINE_OCTETS - 1  # continuación: 1 octeto es el espacio
    return "\r\n ".join(part.decode("utf-8") for part in parts)


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
    multistatus_xml: str, *, tz: tzinfo, calendar: str | None = None
) -> list[CalendarEvent]:
    """Extrae, normaliza a **UTC-aware** y **deduplica** los VEVENT de un ``calendar-query``.

    ``tz`` es la zona del usuario: se usa para interpretar las horas flotantes (sin
    ``Z`` ni ``TZID``) y para enmarcar los eventos ``VALUE=DATE`` (todo-el-día) como
    el día local completo. Las horas con ``Z`` o ``TZID`` traen su propia zona.

    DEDUP por ``(UID, RECURRENCE-ID)``: la expansión server-side puede devolver una
    ocurrencia tanto como instancia regular como su *override* (mismo ``RECURRENCE-ID``)
    — se conserva la primera y se evita el doble-conteo visto en smoke. Las ocurrencias
    distintas de una serie (mismo ``UID``, ``RECURRENCE-ID`` distinto) se conservan
    todas. Los VEVENT sin ``UID`` nunca se colapsan.
    """
    root = ET.fromstring(multistatus_xml)
    raws: list[_RawVEvent] = []
    for data_el in root.iter(f"{_CALDAV_NS}calendar-data"):
        if data_el.text:
            raws.extend(_parse_vevents(data_el.text, calendar=calendar, tz=tz))

    events: list[CalendarEvent] = []
    seen: set[tuple[str, str]] = set()
    for raw in raws:
        if raw.uid is not None:
            # Sin RECURRENCE-ID (maestro/instancia sin recid), se distingue por el
            # inicio concreto para no colapsar ocurrencias expandidas de un mismo UID.
            occurrence = raw.recurrence_id or raw.event.start.isoformat()
            key = (raw.uid, occurrence)
            if key in seen:
                continue
            seen.add(key)
        events.append(raw.event)
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


def _params(name: str) -> dict[str, str]:
    """Parámetros de una propiedad iCal (``DTSTART;TZID=...;VALUE=...``) → dict en MAYÚS."""
    out: dict[str, str] = {}
    for part in name.split(";")[1:]:
        if "=" in part:
            key, val = part.split("=", 1)
            out[key.strip().upper()] = val.strip()
    return out


def _parse_ical_datetime(
    name: str, value: str, tz: tzinfo
) -> tuple[datetime, bool] | None:
    """Parsea DTSTART/DTEND a ``(datetime UTC-aware, all_day)`` o ``None`` si no encaja.

    Reglas de zona (Bloque 2.1), todo normalizado a UTC:
    * ``...Z``            → UTC.
    * ``;TZID=<zona>``    → se localiza con esa zona y se convierte a UTC (si la zona
      es desconocida, se cae a ``tz`` del usuario).
    * ``VALUE=DATE``/``YYYYMMDD`` (todo-el-día) → medianoche **local** del día (``tz``)
      convertida a UTC.
    * flotante (sin ``Z`` ni ``TZID``) → se asume la zona del usuario (``tz``).
    """
    value = value.strip()
    params = _params(name)
    is_date = params.get("VALUE", "").upper() == "DATE"
    try:
        if is_date or (len(value) == 8 and value.isdigit()):
            local = datetime.strptime(value, _ICAL_DATE).replace(tzinfo=tz)
            return (local.astimezone(timezone.utc), True)
        if value.endswith("Z"):
            return (
                datetime.strptime(value, _ICAL_DT_UTC).replace(tzinfo=timezone.utc),
                False,
            )
        naive = datetime.strptime(value, _ICAL_DT_LOCAL)
        zone = to_zoneinfo(params["TZID"], default=tz) if "TZID" in params else tz
        return (naive.replace(tzinfo=zone).astimezone(timezone.utc), False)
    except ValueError:
        return None


def _parse_vevents(
    ical_text: str, *, calendar: str | None, tz: tzinfo
) -> list[_RawVEvent]:
    """Recorre el VCALENDAR y emite un `_RawVEvent` por cada VEVENT con DTSTART válido.

    Captura ``UID`` y ``RECURRENCE-ID`` para deduplicar aguas arriba, y marca
    ``recurring=True`` si el VEVENT trae ``RRULE`` (maestro sin expandir).
    """
    raws: list[_RawVEvent] = []
    in_event = False
    summary = ""
    uid: str | None = None
    recurrence_id: str | None = None
    has_rrule = False
    dtstart: tuple[datetime, bool] | None = None
    dtend: tuple[datetime, bool] | None = None

    for line in _unfold(ical_text):
        stripped = line.strip()
        if stripped == "BEGIN:VEVENT":
            in_event, summary, uid, recurrence_id, has_rrule = True, "", None, None, False
            dtstart = dtend = None
            continue
        if stripped == "END:VEVENT":
            if dtstart is not None:
                start_dt, all_day = dtstart
                raws.append(
                    _RawVEvent(
                        event=CalendarEvent(
                            summary=summary or "(sin título)",
                            start=start_dt,
                            end=dtend[0] if dtend is not None else None,
                            all_day=all_day,
                            calendar=calendar,
                            recurring=has_rrule,
                        ),
                        uid=uid,
                        recurrence_id=recurrence_id,
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
        elif prop == "UID":
            uid = value.strip() or None
        elif prop == "RECURRENCE-ID":
            recurrence_id = value.strip() or None
        elif prop == "RRULE":
            has_rrule = True
        elif prop == "DTSTART":
            dtstart = _parse_ical_datetime(name, value, tz)
        elif prop == "DTEND":
            dtend = _parse_ical_datetime(name, value, tz)

    return raws

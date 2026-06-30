"""Adapter de Calendar (CalDAV) sobre Nextcloud — implementa `CalendarPort` (SOLO lectura).

Encapsula su **propio** cliente HTTP firmado: replica la auth de AppAPI
(``AUTHORIZATION-APP-API: base64(uid:app_secret)``) para **impersonar** al usuario,
SIN tocar el adaptador privado ``nc._session.adapter`` (deuda **D-IMP-1** de
ADR-016). El ``app_secret`` **NUNCA** se loguea.

Flujo (read-only): PROPFIND ``/calendars/<uid>/`` (Depth 1) para descubrir las
colecciones-calendario, y por cada una un REPORT ``calendar-query`` con ``time-range``
+ **expansión server-side** (``<C:expand>``) de las recurrencias. La expansión hace
que las consultas a fechas futuras vean las ocurrencias de eventos recurrentes (el
maestro tiene su ``DTSTART`` en el pasado y no caería en el rango). NO se implementa
motor RRULE en cliente: si el servidor ignora ``<C:expand>`` (devuelve maestros con
``RRULE``), se registra un warning y se devuelve lo disponible (deuda explícita).
El parseo del multistatus/iCal se **delega** a ``domain.caldav`` (puro, testeable
offline). El ``transport`` es inyectable para tests sin red.

Regla de capas (§3): toca infraestructura (red, CalDAV) ⇒ es un **adapter**; habla
con el resto del sistema solo a través del contrato `CalendarPort` y los value
objects de dominio.
"""
from __future__ import annotations

import base64
import logging
from urllib.parse import quote, urlsplit

import httpx

from app.domain.caldav import (
    PROPFIND_CALENDARS_BODY,
    build_calendar_query,
    parse_calendar_hrefs,
    parse_events,
)
from app.domain.calendar import CalendarEvent, DateRange

logger = logging.getLogger(__name__)

_MULTISTATUS = 207
_XML_CONTENT_TYPE = "application/xml; charset=utf-8"


class CalendarError(Exception):
    """Fallo del adapter de calendario (transporte, HTTP, respuesta inesperada)."""


class NextcloudCalendarAdapter:
    """Implementa `CalendarPort` contra CalDAV de Nextcloud, impersonando al usuario."""

    def __init__(
        self,
        *,
        endpoint: str,
        app_id: str,
        app_version: str,
        app_secret: str,
        aa_version: str = "2.2.0",
        dav_url_suffix: str = "remote.php/dav",
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not endpoint or not app_id or not app_secret:
            raise CalendarError(
                "NEXTCLOUD_URL, APP_ID y APP_SECRET son obligatorios para el "
                "adapter de calendario impersonado."
            )
        # Normaliza igual que nc_py_api: raíz NC sin /index.php ni barra final.
        self._endpoint = endpoint.removesuffix("/index.php").rstrip("/")
        self._dav_suffix = "/" + dav_url_suffix.strip("/")
        self._app_id = app_id
        self._app_version = app_version
        self._app_secret = app_secret  # NUNCA se loguea
        self._aa_version = aa_version
        self._timeout_s = timeout_s
        self._transport = transport

    async def list_events(
        self, uid: str, date_range: DateRange
    ) -> list[CalendarEvent]:
        if not uid:
            raise CalendarError("uid vacío: no hay identidad que impersonar.")

        headers = self._headers(uid)
        calendars_path = f"{self._dav_suffix}/calendars/{quote(uid)}/"
        events: list[CalendarEvent] = []

        async with httpx.AsyncClient(
            base_url=self._endpoint,
            timeout=self._timeout_s,
            transport=self._transport,
        ) as client:
            propfind = await client.request(
                "PROPFIND",
                calendars_path,
                headers={**headers, "Depth": "1", "Content-Type": _XML_CONTENT_TYPE},
                content=PROPFIND_CALENDARS_BODY,
            )
            if propfind.status_code != _MULTISTATUS:
                raise CalendarError(
                    f"PROPFIND de calendarios devolvió HTTP {propfind.status_code} "
                    f"(uid={uid!r})."
                )
            hrefs = parse_calendar_hrefs(propfind.text)
            logger.info("Calendarios descubiertos para %s: %d.", uid, len(hrefs))

            query_body = build_calendar_query(date_range)
            for href in hrefs:
                path = _href_path(href)
                report = await client.request(
                    "REPORT",
                    path,
                    headers={
                        **headers,
                        "Depth": "1",
                        "Content-Type": _XML_CONTENT_TYPE,
                    },
                    content=query_body,
                )
                if report.status_code != _MULTISTATUS:
                    logger.warning(
                        "REPORT en %s devolvió HTTP %s; se omite ese calendario.",
                        path,
                        report.status_code,
                    )
                    continue
                events.extend(
                    parse_events(
                        report.text,
                        tz=date_range.tz,
                        calendar=_calendar_name(href),
                    )
                )

        # Si llegó algún maestro con RRULE, el servidor NO honró <C:expand>: las
        # ocurrencias futuras faltarán. Se avisa y se devuelve lo disponible; NO se
        # expande en cliente (deuda explícita, fuera de scope).
        if any(e.recurring for e in events):
            logger.warning(
                "El servidor CalDAV no expandió las recurrencias (<C:expand> "
                "ignorado): llegaron maestros con RRULE para uid=%s. Las ocurrencias "
                "futuras de eventos recurrentes pueden faltar (deuda: sin expansión "
                "en cliente).",
                uid,
            )

        # Filtro de pertenencia al rango aware-vs-aware: el time-range del servidor es
        # una criba gruesa; aquí se confirma que el inicio cae en la ventana local
        # del usuario (defensa frente a bordes/expansiones del servidor).
        in_range = [e for e in events if date_range.contains(e.start)]
        in_range.sort(key=lambda e: (e.start, e.summary))
        return in_range

    def _headers(self, uid: str) -> dict[str, str]:
        """Cabeceras AppAPI firmadas que impersonan a ``uid``. El secreto no se loguea."""
        token = base64.b64encode(
            f"{uid}:{self._app_secret}".encode("utf-8")
        ).decode("ascii")
        return {
            "AA-VERSION": self._aa_version,
            "EX-APP-ID": self._app_id,
            "EX-APP-VERSION": self._app_version,
            "OCS-APIRequest": "true",
            "AUTHORIZATION-APP-API": token,
            "User-Agent": f"ExApp/{self._app_id}/{self._app_version}",
        }


def _href_path(href: str) -> str:
    """Normaliza un href a una ruta servida desde la raíz (descarta esquema/host)."""
    if href.startswith("http://") or href.startswith("https://"):
        return urlsplit(href).path
    return href


def _calendar_name(href: str) -> str | None:
    """Último segmento del href como pista del calendario (p. ej. ``personal``)."""
    return _href_path(href).rstrip("/").rsplit("/", 1)[-1] or None

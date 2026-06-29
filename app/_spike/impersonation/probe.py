"""SPIKE — REMOVE BEFORE MERGE.

Read-only impersonation probe for ADR-016. Builds an AsyncNextcloudApp, calls
set_user(SPIKE_TARGET_UID), and exercises three endpoints under that identity,
capturing the raw HTTP status of each (NO nc_py_api exception wrapping, so 401/
403 surface as data, not as raised errors):

  * identity  GET  /ocs/v1.php/cloud/user                 -> who does NC think we are?
  * calendar  PROPFIND /remote.php/dav/calendars/<uid>/   -> list the user's calendars
  * deck      GET  /index.php/apps/deck/api/v1.0/boards   -> list the user's Deck boards

Why the private `nc._session.adapter` / `adapter_dav`:
  nc_py_api 0.30.1 exposes NO public "raw request" method — only the high-level
  `ocs()` (which unwraps the OCS envelope and *raises* on non-2xx) and the typed
  Files/Sharing APIs. CalDAV and the Deck REST API are neither OCS nor Files, so
  the spike reaches for the underlying niquests adapters directly. This coupling
  to a private attribute is acceptable for throwaway code; productive skills must
  not depend on it (registered as a finding in the spike doc).

Impersonation mechanism (verified by source reading, _session.py _add_auth):
  In 0.30.1 the impersonated uid travels INSIDE the `AUTHORIZATION-APP-API`
  header as base64(f"{uid}:{app_secret}"). There is NO separate `EX-APP-USER-ID`
  header in this library version. The app secret is NEVER logged by this spike.

Inputs (environment):
  SPIKE_TARGET_UID                 uid to impersonate (the invoking user). Required.
  SPIKE_IMPERSONATION_DECK_PATH    Deck boards endpoint override (default below).
"""
from __future__ import annotations

import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

from nc_py_api import AsyncNextcloudApp

logger = logging.getLogger(__name__)

_DECK_BOARDS_DEFAULT = "/index.php/apps/deck/api/v1.0/boards"

# Minimal CalDAV PROPFIND body: ask for resourcetype (to tell a calendar from a
# plain collection), the display name, and the supported component set.
_CALDAV_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"'
    ' xmlns:cal="urn:ietf:params:xml:ns:caldav"'
    ' xmlns:cs="http://calendarserver.org/ns/">'
    "<d:prop>"
    "<d:resourcetype/>"
    "<d:displayname/>"
    "<cal:supported-calendar-component-set/>"
    "<cs:getctag/>"
    "</d:prop>"
    "</d:propfind>"
)

_DAV_NS = "{DAV:}"
_CALDAV_NS = "{urn:ietf:params:xml:ns:caldav}"


def _env(name: str, default: str) -> str:
    val = os.environ.get(name, "").strip()
    return val if val else default


async def _raw(adapter, method: str, path: str, *, headers=None, data=None) -> tuple[dict[str, Any], Any]:
    """Issue one raw request via a niquests adapter.

    Returns (meta, response_or_None). HTTP status NEVER raises here — only a
    transport-level failure (DNS, connection) is captured as `transport_error`.
    `denied` is set for 401/403 with a short body excerpt for diagnosis.
    """
    t0 = time.perf_counter()
    try:
        resp = await adapter.request(method, path, headers=headers, data=data)
    except Exception as exc:  # transport error, not an HTTP status
        return (
            {"ok": False, "transport_error": repr(exc),
             "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 1)},
            None,
        )
    status = resp.status_code
    meta: dict[str, Any] = {
        "ok": status < 400,
        "http_status": status,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 1),
    }
    if status in (401, 403):
        meta["denied"] = True
        meta["error_excerpt"] = (resp.text or "")[:400]
    return (meta, resp)


def _parse_calendars(xml_text: str) -> list[dict[str, Any]]:
    """Extract calendar collections from a CalDAV multistatus response.

    A <response> is a calendar when its <resourcetype> contains the CalDAV
    <calendar/> element (plain collections and the home node itself are skipped).
    """
    root = ET.fromstring(xml_text)
    calendars: list[dict[str, Any]] = []
    for response in root.findall(f"{_DAV_NS}response"):
        rtype = response.find(f".//{_DAV_NS}resourcetype")
        is_calendar = rtype is not None and rtype.find(f"{_CALDAV_NS}calendar") is not None
        if not is_calendar:
            continue
        href_el = response.find(f"{_DAV_NS}href")
        dn_el = response.find(f".//{_DAV_NS}displayname")
        calendars.append(
            {
                "href": href_el.text if href_el is not None else None,
                "display_name": dn_el.text if dn_el is not None else None,
            }
        )
    return calendars


async def _probe_identity(nc: AsyncNextcloudApp) -> dict[str, Any]:
    """H1 cross-check: who does Nextcloud resolve us as, under impersonation?"""
    meta, resp = await _raw(nc._session.adapter, "GET", "/ocs/v1.php/cloud/user")
    result: dict[str, Any] = {
        "call": "GET /ocs/v1.php/cloud/user",
        "configured_identity": await nc.user,
        **meta,
    }
    if resp is not None and resp.status_code < 400:
        try:
            data = json.loads(resp.text)
            result["server_resolved_id"] = (
                data.get("ocs", {}).get("data", {}).get("id")
            )
        except Exception as exc:  # noqa: BLE001 — spike, surface raw
            result["parse_error"] = repr(exc)
            result["raw_excerpt"] = (resp.text or "")[:400]
    return result


async def _probe_calendar(nc: AsyncNextcloudApp, target_uid: str) -> dict[str, Any]:
    """H2: list the impersonated user's calendars via a CalDAV PROPFIND."""
    path = f"/calendars/{quote(target_uid)}/"
    meta, resp = await _raw(
        nc._session.adapter_dav,
        "PROPFIND",
        path,
        headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        data=_CALDAV_PROPFIND_BODY,
    )
    result: dict[str, Any] = {
        "call": f"PROPFIND {nc._session.cfg.dav_url_suffix}{path}",
        "configured_identity": await nc.user,
        **meta,
    }
    if resp is not None and resp.status_code == 207:  # 207 Multistatus
        try:
            calendars = _parse_calendars(resp.text)
            result["calendar_count"] = len(calendars)
            result["calendars"] = calendars
        except Exception as exc:  # noqa: BLE001 — spike, surface raw
            result["parse_error"] = repr(exc)
            result["raw_excerpt"] = (resp.text or "")[:400]
    return result


async def _probe_deck(nc: AsyncNextcloudApp, deck_path: str) -> dict[str, Any]:
    """H3: list the impersonated user's Deck boards via the Deck REST API."""
    meta, resp = await _raw(
        nc._session.adapter, "GET", deck_path, headers={"Accept": "application/json"}
    )
    result: dict[str, Any] = {
        "call": f"GET {deck_path}",
        "configured_identity": await nc.user,
        **meta,
    }
    if resp is not None and resp.status_code < 400:
        try:
            boards = json.loads(resp.text)
            board_list = boards if isinstance(boards, list) else []
            result["board_count"] = len(board_list)
            result["boards"] = [
                {"id": b.get("id"), "title": b.get("title")}
                for b in board_list
                if isinstance(b, dict)
            ][:50]
        except Exception as exc:  # noqa: BLE001 — spike, surface raw
            result["parse_error"] = repr(exc)
            result["raw_excerpt"] = (resp.text or "")[:400]
    return result


def _infer_missing_scopes(calls: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Heuristic — NOT authoritative.

    An HTTP 401/403 does not name an AppAPI scope; the exact scope must be
    confirmed via `occ app_api:app:register --json-info '{"scopes":[...]}'` and
    the AppAPI docs. This only flags WHICH probe was denied and proposes a
    candidate to investigate. All candidates are TBD.
    """
    suspects: list[dict[str, Any]] = []
    if calls.get("identity", {}).get("denied"):
        suspects.append({
            "probe": "identity",
            "http_status": calls["identity"].get("http_status"),
            "candidate_scope": "TBD — impersonation not honored (HaRP / EX-APP-USER-ID mapping?)",
        })
    if calls.get("calendar", {}).get("denied"):
        suspects.append({
            "probe": "calendar",
            "http_status": calls["calendar"].get("http_status"),
            "candidate_scope": "TBD — DAV/CalDAV access scope?",
        })
    if calls.get("deck", {}).get("denied"):
        suspects.append({
            "probe": "deck",
            "http_status": calls["deck"].get("http_status"),
            "candidate_scope": "TBD — no core AppAPI 'Deck' scope; Deck app reachability for ExApps?",
        })
    return suspects


async def run_probe() -> dict[str, Any]:
    """Execute the impersonation probe end to end; return a JSON-serializable report."""
    target_uid = _env("SPIKE_TARGET_UID", "")
    deck_path = _env("SPIKE_IMPERSONATION_DECK_PATH", _DECK_BOARDS_DEFAULT)

    nc = AsyncNextcloudApp()
    cfg = nc._session.cfg

    report: dict[str, Any] = {
        "spike": "impersonation",
        "read_only": True,
        "inputs": {"target_uid": target_uid, "deck_path": deck_path},
        "app": {
            "app_id": cfg.app_name,
            "aa_version": cfg.aa_version,
            "endpoint": cfg.endpoint,
            "dav_endpoint": cfg.dav_endpoint,
        },
        "impersonation_mechanism": (
            "nc_py_api 0.30.1 encodes the impersonated uid INSIDE the "
            "AUTHORIZATION-APP-API header as b64(uid:app_secret) "
            "(_session.py::_add_auth). No separate EX-APP-USER-ID header is sent "
            "by this library version. The app secret is NOT logged."
        ),
        "calls": {},
        "scopes_missing": [],
    }

    if not target_uid:
        report["fatal"] = "SPIKE_TARGET_UID is empty; set it to the invoking user's uid."
        return report

    # ---- H1: set_user must not be rejected -------------------------------
    # set_user() also fires an OCS capabilities call under the NEW identity, so
    # a rejected impersonation surfaces right here (and _user is already set, so
    # the per-call probes below still run under the impersonated identity).
    try:
        await nc.set_user(target_uid)
        report["set_user_ok"] = True
        report["configured_identity"] = await nc.user
    except Exception as exc:  # noqa: BLE001 — spike, surface raw
        report["set_user_ok"] = False
        report["set_user_error"] = repr(exc)

    # ---- Per-endpoint probes (raw status, read-only) ---------------------
    report["calls"]["identity"] = await _probe_identity(nc)
    report["calls"]["calendar"] = await _probe_calendar(nc, target_uid)
    report["calls"]["deck"] = await _probe_deck(nc, deck_path)

    report["scopes_missing"] = _infer_missing_scopes(report["calls"])
    return report

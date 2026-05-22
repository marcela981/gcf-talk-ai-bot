"""SPIKE — REMOVE BEFORE MERGE.

Validates whether nc_py_api 0.30.x is sufficient to use Nextcloud Files as
the source of truth for the RAG corpus (ADR-006). Read-only.

Covered hypotheses (cf. docs/spikes/SPIKE_NEXTCLOUD_FILES.md):
  H1  AsyncNextcloudApp exposes Files + Tags + Sharing in async form.
  H2  listdir() returns name + mtime + etag + size + mime.
  H3  Streaming download to a file path works (no full-buffer load).
  H4  get_tags() returns SystemTag list for a given FsNode/file_id.
  H5  sharing.get_list() returns ShareType.TYPE_GROUP entries with permissions.
  H6  ExApp can read group-shared files without per-user login (set_user vs
      app-only identity).
  H7  nc_py_api exposes a sync token / incremental-change API. (See verdict
      in the markdown — fallback is folder etag polling.)

The spike is invoked in two ways:
  * As a one-shot module:  python -m app._spike.nextcloud_files_spike
  * From the temporary endpoint POST /debug/files-spike in app/main.py.

Inputs come from environment variables so the spike can be re-run against
different setups without code edits:
    SPIKE_FILES_ROOT_PATH   path inside the owner's user root (default
                            "AI-Corpus/finanzas"). Leading slash optional.
    SPIKE_FILES_OWNER_UID   uid that owns /AI-Corpus (default "admin").
    SPIKE_IMPERSONATE_AS    optional uid to set_user() before phase 3
                            (e.g. "user_finanzas"). Empty = stay as
                            SPIKE_FILES_OWNER_UID.
    SPIKE_ITERATIONS        iterations for p50/p95 measurement (default 5).
"""
from __future__ import annotations

import asyncio
import logging
import os
import statistics
import time
from typing import Any

# nc_py_api 0.30.x async surface (cf. nc_py_api/files/files_async.py and
# nc_py_api/files/sharing.py for the full method list).
from nc_py_api import AsyncNextcloudApp
from nc_py_api.files import FsNode, Share, ShareType, SystemTag

logger = logging.getLogger(__name__)

# Tmp output directory for streamed downloads. /tmp is writable inside the
# container; on Windows dev hosts os.path.join handles the separator.
_TMP_DIR = "/tmp"


def _env(name: str, default: str) -> str:
    val = os.environ.get(name, "").strip()
    return val if val else default


def _norm_path(p: str) -> str:
    """Normalize SPIKE_FILES_ROOT_PATH to nc_py_api's user_path form (no leading slash)."""
    return p.strip().lstrip("/")


def _percentile(samples_ms: list[float], pct: float) -> float:
    """Inclusive percentile. For n=5 and pct=50 returns the 3rd sample; pct=95 returns the max."""
    if not samples_ms:
        return 0.0
    if len(samples_ms) == 1:
        return samples_ms[0]
    s = sorted(samples_ms)
    k = (len(s) - 1) * (pct / 100.0)
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


async def _phase_listdir(nc: AsyncNextcloudApp, root_path: str) -> list[dict[str, Any]]:
    """Phase (a): list a folder and project FsNode → JSON-friendly dict.

    listdir(depth=1) returns the folder itself + children when exclude_self=False,
    or only children when exclude_self=True (the default). The spike asks
    explicitly for the contents.
    """
    nodes: list[FsNode] = await nc.files.listdir(root_path, depth=1, exclude_self=True)
    return [
        {
            "name": n.name,
            "user_path": n.user_path,
            "is_dir": n.is_dir,
            # FsNode.info.last_modified is a tz-aware datetime; cast to ISO so
            # we can serialize it through the debug endpoint.
            "mtime": n.info.last_modified.isoformat() if n.info.last_modified else None,
            "etag": n.etag,
            "size": n.info.size,
            "mime": n.info.mimetype,
            "fileid": n.info.fileid,
            "permissions": n.info.permissions,
        }
        for n in nodes
    ]


async def _phase_stream_download(nc: AsyncNextcloudApp, root_path: str) -> dict[str, Any]:
    """Phase (b): stream the first PDF in the listing to /tmp.

    Uses download2stream so the file is written chunk-by-chunk (default 5MiB
    chunk size in nc_py_api) instead of buffering the whole payload in memory.
    """
    children: list[FsNode] = await nc.files.listdir(root_path, depth=1, exclude_self=True)
    pdf = next(
        (n for n in children if not n.is_dir and n.info.mimetype == "application/pdf"),
        None,
    )
    if pdf is None:
        return {"skipped": True, "reason": "no application/pdf in listing", "files_seen": [n.name for n in children]}

    local_path = os.path.join(_TMP_DIR, f"spike_{pdf.name}")
    with open(local_path, "wb") as fp:
        await nc.files.download2stream(pdf, fp)

    return {
        "skipped": False,
        "downloaded": pdf.name,
        "local_path": local_path,
        "bytes_on_disk": os.path.getsize(local_path),
        "etag_at_download": pdf.etag,
        "fileid": pdf.info.fileid,
    }


async def _phase_tags(nc: AsyncNextcloudApp, root_path: str) -> dict[str, Any]:
    """Phase (c): list tags assigned to the first PDF in the folder."""
    children: list[FsNode] = await nc.files.listdir(root_path, depth=1, exclude_self=True)
    pdf = next((n for n in children if not n.is_dir), None)
    if pdf is None:
        return {"skipped": True, "reason": "folder empty"}

    tags: list[SystemTag] = await nc.files.get_tags(pdf)
    return {
        "skipped": False,
        "file": pdf.name,
        "fileid": pdf.info.fileid,
        "tags": [{"id": t.tag_id, "name": t.display_name, "user_visible": t.user_visible} for t in tags],
    }


async def _phase_shares(nc: AsyncNextcloudApp, root_path: str) -> dict[str, Any]:
    """Phase (d): list shares for the root folder.

    sharing.get_list(path=...) returns shares CREATED on that path; we want to
    know which groups the folder is exposed to and with what permissions.
    """
    shares: list[Share] = await nc.files.sharing.get_list(path=root_path)
    return {
        "count": len(shares),
        "shares": [
            {
                "share_id": s.share_id,
                "share_type": s.share_type.name,
                "is_group": s.share_type == ShareType.TYPE_GROUP,
                "share_with": s.share_with,
                "permissions_int": int(s.permissions),
                "share_owner": s.share_owner,
                "file_owner": s.file_owner,
                "path": s.path,
            }
            for s in shares
        ],
    }


async def _measure(coro_factory, iterations: int) -> dict[str, float]:
    """Run an awaitable factory `iterations` times and report p50/p95/min/max in ms."""
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        await coro_factory()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return {
        "iterations": iterations,
        "p50_ms": round(_percentile(samples, 50), 2),
        "p95_ms": round(_percentile(samples, 95), 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
        "mean_ms": round(statistics.fmean(samples), 2),
        "samples_ms": [round(x, 2) for x in samples],
    }


async def run_spike() -> dict[str, Any]:
    """Execute the spike end to end and return a JSON-serializable report."""
    root_path = _norm_path(_env("SPIKE_FILES_ROOT_PATH", "AI-Corpus/finanzas"))
    owner_uid = _env("SPIKE_FILES_OWNER_UID", "admin")
    impersonate_as = _env("SPIKE_IMPERSONATE_AS", "")
    iterations = int(_env("SPIKE_ITERATIONS", "5"))

    nc = AsyncNextcloudApp()
    # Default ExApp identity is empty (auth is shared-secret, not per-user).
    # File traversal needs a user context — set it to the folder owner so DAV
    # paths resolve under /remote.php/dav/files/<owner>/...
    await nc.set_user(owner_uid)
    identity_after_set = await nc.user

    report: dict[str, Any] = {
        "spike": "nextcloud_files",
        "inputs": {
            "root_path": root_path,
            "owner_uid": owner_uid,
            "impersonate_as": impersonate_as,
            "iterations": iterations,
        },
        "identity": {
            "after_set_user": identity_after_set,
            "app_id": nc.app_cfg.app_name,
            "app_api_version": nc.app_cfg.aa_version,
        },
    }

    # ---- Phase (a) listdir ------------------------------------------------
    try:
        report["a_listdir"] = await _phase_listdir(nc, root_path)
    except Exception as exc:  # pragma: no cover — spike, surface raw error
        report["a_listdir_error"] = repr(exc)

    # ---- Phase (b) streaming download ------------------------------------
    try:
        report["b_stream_download"] = await _phase_stream_download(nc, root_path)
    except Exception as exc:
        report["b_stream_download_error"] = repr(exc)

    # ---- Phase (c) tags --------------------------------------------------
    try:
        report["c_tags"] = await _phase_tags(nc, root_path)
    except Exception as exc:
        report["c_tags_error"] = repr(exc)

    # ---- Phase (d) shares ------------------------------------------------
    try:
        report["d_shares"] = await _phase_shares(nc, root_path)
    except Exception as exc:
        report["d_shares_error"] = repr(exc)

    # ---- Phase (e) p50/p95 of (a) and (b) --------------------------------
    try:
        report["e_metrics"] = {
            "listdir": await _measure(lambda: _phase_listdir(nc, root_path), iterations),
            "stream_download": await _measure(lambda: _phase_stream_download(nc, root_path), iterations),
        }
    except Exception as exc:
        report["e_metrics_error"] = repr(exc)

    # ---- Impersonation probe (H6) ---------------------------------------
    # Switch identity and try the same listdir. If the folder is shared with
    # the impersonated user's group, listdir under their root should succeed.
    if impersonate_as:
        try:
            await nc.set_user(impersonate_as)
            identity_now = await nc.user
            try:
                impersonated_nodes = await nc.files.listdir(root_path, depth=1, exclude_self=True)
                impersonation_result: dict[str, Any] = {
                    "identity_after_set": identity_now,
                    "listdir_ok": True,
                    "count": len(impersonated_nodes),
                }
            except Exception as inner:
                impersonation_result = {
                    "identity_after_set": identity_now,
                    "listdir_ok": False,
                    "error": repr(inner),
                }
            report["impersonation"] = impersonation_result
        except Exception as exc:
            report["impersonation_error"] = repr(exc)

    return report


def _main() -> None:  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(run_spike())
    import json

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    _main()

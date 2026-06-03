"""Tests del SupabaseDocumentsTableLoader sin red (httpx.MockTransport).

Cubre el contrato de ADR-006-ter: filtro por access_level, descarte de
placeholders / filas sin url, role_scope literal, source=id, y reintentos con
backoff ante timeouts en la descarga.
"""
from __future__ import annotations

import httpx
import pytest

from app.adapters.supabase_documents_table_loader import (
    DocumentsTableError,
    SupabaseDocumentsTableLoader,
)

BASE = "https://catalog.supabase.co"


def _rows() -> list[dict]:
    return [
        {"id": 1, "name": "manual.pdf", "url": "https://files.example/manual.pdf",
         "access_level": "noroot", "folder_id": 1},
        {"id": 2, "name": "interno.pdf", "url": "https://files.example/interno.pdf",
         "access_level": "root", "folder_id": 1},
        {"id": 3, "name": "semi.md", "url": "https://files.example/semi.md",
         "access_level": "semiroot", "folder_id": 2},
        {"id": 4, "name": ".emptyFolderPlaceholder", "url": "https://files.example/x",
         "access_level": "noroot", "folder_id": 3},
        {"id": 5, "name": "huerfano.pdf", "url": "", "access_level": "noroot",
         "folder_id": 3},
    ]


def _make_loader(rows, files, *, levels, max_retries=3) -> SupabaseDocumentsTableLoader:
    """`files`: dict url -> bytes | int(status) | callable(request)->Response."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rest/v1/documents"):
            offset = int(request.url.params.get("offset", "0"))
            return httpx.Response(200, json=rows if offset == 0 else [])
        target = str(request.url)
        if target in files:
            spec = files[target]
            if callable(spec):
                return spec(request)
            if isinstance(spec, int):
                return httpx.Response(spec)
            return httpx.Response(200, content=spec)
        return httpx.Response(404)

    return SupabaseDocumentsTableLoader(
        base_url=BASE,
        api_key="anon-key",
        levels=levels,
        max_retries=max_retries,
        retry_backoff_s=0.0,  # sin esperas reales en test
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_filters_by_access_level_and_preserves_literal_scope():
    loader = _make_loader(_rows(), {}, levels=["noroot"])

    docs = await loader.list_documents()

    # Solo la fila noroot válida (id=1); placeholder (4) y sin-url (5) descartados.
    assert [d.source for d in docs] == ["1"]
    d = docs[0]
    assert d.role_scope == "noroot"  # LITERAL, sin aplanar a "public"
    assert d.path == "https://files.example/manual.pdf"  # locator = url tal cual
    assert d.content_type == "application/pdf"
    assert d.source == "1"  # source = str(id)


@pytest.mark.asyncio
async def test_multiple_levels_keep_their_own_literal_scope():
    loader = _make_loader(_rows(), {}, levels=["noroot", "root", "semiroot"])

    docs = await loader.list_documents()

    scopes = {d.source: d.role_scope for d in docs}
    assert scopes == {"1": "noroot", "2": "root", "3": "semiroot"}


@pytest.mark.asyncio
async def test_skips_placeholder_and_rows_without_url():
    loader = _make_loader(_rows(), {}, levels=["noroot"])

    docs = await loader.list_documents()

    sources = {d.source for d in docs}
    assert "4" not in sources  # .emptyFolderPlaceholder
    assert "5" not in sources  # url vacía


@pytest.mark.asyncio
async def test_download_returns_raw_bytes_without_auth_headers():
    captured: dict[str, httpx.Headers] = {}

    def serve(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, content=b"%PDF-1.4 ...")

    files = {"https://files.example/manual.pdf": serve}
    loader = _make_loader(_rows(), files, levels=["noroot"])

    data = await loader.download("https://files.example/manual.pdf")

    assert data == b"%PDF-1.4 ..."
    # La descarga es a OTRO proyecto: NO debe llevar la apikey de la tabla.
    assert "apikey" not in captured["headers"]
    assert "authorization" not in captured["headers"]


@pytest.mark.asyncio
async def test_download_retries_on_timeout_then_raises():
    calls = {"n": 0}

    def always_timeout(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectTimeout("boom", request=request)

    files = {"https://files.example/manual.pdf": always_timeout}
    loader = _make_loader(_rows(), files, levels=["noroot"], max_retries=3)

    with pytest.raises(DocumentsTableError):
        await loader.download("https://files.example/manual.pdf")

    assert calls["n"] == 3  # 3 intentos antes de rendirse


@pytest.mark.asyncio
async def test_download_recovers_after_transient_timeout():
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("boom", request=request)
        return httpx.Response(200, content=b"ok")

    files = {"https://files.example/manual.pdf": flaky}
    loader = _make_loader(_rows(), files, levels=["noroot"], max_retries=3)

    data = await loader.download("https://files.example/manual.pdf")

    assert data == b"ok"
    assert calls["n"] == 2  # falló una vez, recuperó al segundo intento

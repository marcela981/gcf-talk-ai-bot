"""Unit tests para NextcloudFilesAdapter (Bloque 2.4), sin red.

Se inyecta un `httpx.MockTransport` (mismo patrón que los tests de Calendar) para ejercer
el adapter sin tocar Nextcloud. Se verifica: el PROPFIND parsea un listado de muestra con el
header de impersonation `AUTHORIZATION-APP-API = b64(uid:app_secret)` (secreto NUNCA en
claro) y Depth 1; el GET devuelve texto; binario/no-UTF-8 y exceso de tamaño → error; 403 y
404 → error como dato; y el rechazo con uid vacío.
"""
from __future__ import annotations

import base64

import httpx
import pytest

from app.adapters.nextcloud_files_adapter import FilesError, NextcloudFilesAdapter

_SECRET = "s3cr3t-app-secret"

# Muestra de PROPFIND Depth 1 sobre la raíz de 'mmazo': self + una carpeta + un archivo.
# El archivo trae su nombre URL-encoded (%20) y un segundo propstat 404 (oc:size no aplica).
_PROPFIND_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:response>
    <d:href>/remote.php/dav/files/mmazo/</d:href>
    <d:propstat>
      <d:prop>
        <d:getlastmodified>Mon, 22 Jun 2026 10:00:00 GMT</d:getlastmodified>
        <d:resourcetype><d:collection/></d:resourcetype>
        <oc:size>123456</oc:size>
        <d:getetag>"root-etag"</d:getetag>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/mmazo/Documentos/</d:href>
    <d:propstat>
      <d:prop>
        <d:getlastmodified>Tue, 23 Jun 2026 11:30:00 GMT</d:getlastmodified>
        <d:resourcetype><d:collection/></d:resourcetype>
        <oc:size>2048</oc:size>
        <d:getetag>"dir-etag"</d:getetag>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/mmazo/informe%20anual.txt</d:href>
    <d:propstat>
      <d:prop>
        <d:getlastmodified>Wed, 24 Jun 2026 09:15:00 GMT</d:getlastmodified>
        <d:getcontentlength>42</d:getcontentlength>
        <d:getcontenttype>text/plain</d:getcontenttype>
        <d:resourcetype/>
        <d:getetag>"file-etag"</d:getetag>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
    <d:propstat>
      <d:prop><oc:size/></d:prop>
      <d:status>HTTP/1.1 404 Not Found</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>"""


def _adapter(handler, *, max_text_bytes: int = 256 * 1024) -> NextcloudFilesAdapter:
    return NextcloudFilesAdapter(
        endpoint="https://nc.example.com",
        app_id="gcf_bot",
        app_version="1.2.3",
        app_secret=_SECRET,
        max_text_bytes=max_text_bytes,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_propfind_lists_files_impersonating_user():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(207, text=_PROPFIND_XML)

    entries = await _adapter(handler).list_files("mmazo", "/")

    # Carpetas primero, luego archivos; el %20 del href se decodifica.
    assert [(e.name, e.is_dir) for e in entries] == [
        ("Documentos", True),
        ("informe anual.txt", False),
    ]
    doc, informe = entries
    assert doc.path == "/Documentos" and doc.size == 2048  # oc:size para carpeta
    assert informe.path == "/informe anual.txt"
    assert informe.size == 42 and informe.mime == "text/plain"
    assert informe.etag == "file-etag"  # sin comillas
    assert informe.modified is not None and informe.modified.year == 2026

    request = seen[0]
    assert request.method == "PROPFIND"
    assert request.url.path == "/remote.php/dav/files/mmazo/"
    assert request.headers["Depth"] == "1"
    assert b"propfind" in request.content

    # Impersonation: uid embebido en AUTHORIZATION-APP-API; secreto NUNCA en claro.
    token = request.headers["AUTHORIZATION-APP-API"]
    assert base64.b64decode(token).decode("utf-8") == f"mmazo:{_SECRET}"
    for key, value in request.headers.items():
        if key.lower() != "authorization-app-api":
            assert _SECRET not in value


@pytest.mark.asyncio
async def test_list_files_targets_subfolder_path():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(207, text=_PROPFIND_XML)

    await _adapter(handler).list_files("mmazo", "Documentos/Proyectos")

    # La ruta se normaliza con '/' inicial y se cuelga de files/<uid>/.
    assert seen[0].url.path == "/remote.php/dav/files/mmazo/Documentos/Proyectos"


@pytest.mark.asyncio
async def test_read_text_file_returns_content_impersonating():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="hola mundo\nsegunda línea")

    text = await _adapter(handler).read_text_file("mmazo", "/Documentos/nota.txt")

    assert text == "hola mundo\nsegunda línea"
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/remote.php/dav/files/mmazo/Documentos/nota.txt"
    token = seen[0].headers["AUTHORIZATION-APP-API"]
    assert base64.b64decode(token).decode("utf-8") == f"mmazo:{_SECRET}"


@pytest.mark.asyncio
async def test_read_binary_file_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG\x00\x01\x02binario")

    with pytest.raises(FilesError, match="binario"):
        await _adapter(handler).read_text_file("mmazo", "/imagen.png")


@pytest.mark.asyncio
async def test_read_non_utf8_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\xc3\x28\xa0\xa1")  # secuencia UTF-8 inválida

    with pytest.raises(FilesError, match="UTF-8"):
        await _adapter(handler).read_text_file("mmazo", "/raro.dat")


@pytest.mark.asyncio
async def test_read_too_large_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x" * 100)  # Content-Length = 100

    adapter = _adapter(handler, max_text_bytes=10)
    with pytest.raises(FilesError, match="límite"):
        await adapter.read_text_file("mmazo", "/grande.txt")


@pytest.mark.asyncio
async def test_read_403_is_error_as_data():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    with pytest.raises(FilesError, match="permiso"):
        await _adapter(handler).read_text_file("mmazo", "/privado.txt")


@pytest.mark.asyncio
async def test_list_404_is_error_as_data():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with pytest.raises(FilesError, match="404"):
        await _adapter(handler).list_files("mmazo", "/NoExiste")


@pytest.mark.asyncio
async def test_empty_uid_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=_PROPFIND_XML)

    adapter = _adapter(handler)
    with pytest.raises(FilesError):
        await adapter.list_files("")
    with pytest.raises(FilesError):
        await adapter.read_text_file("", "/a.txt")

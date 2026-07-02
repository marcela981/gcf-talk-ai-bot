"""Unit tests para el parser puro de Files (`app.domain.files`), sin red — Bloque 2.4.

Complementa los tests del adapter: aquí se ejerce `parse_directory` en aislamiento
(exclusión del self, propstat 404 ignorado, `oc:size` de carpetas, mtime → datetime aware,
etag sin comillas y decodificación del href).
"""
from __future__ import annotations

from app.domain.files import parse_directory

_BASE = "/remote.php/dav/files/mmazo"


def _ms(*responses: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
        + "".join(responses)
        + "</d:multistatus>"
    )


_SELF = (
    "<d:response><d:href>/remote.php/dav/files/mmazo/</d:href>"
    "<d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>"
    "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
)


def test_self_entry_is_excluded():
    assert parse_directory(_ms(_SELF), base_prefix=_BASE) == []


def test_folder_uses_oc_size_and_file_uses_contentlength():
    folder = (
        "<d:response><d:href>/remote.php/dav/files/mmazo/Sub/</d:href>"
        "<d:propstat><d:prop>"
        "<d:resourcetype><d:collection/></d:resourcetype><oc:size>4096</oc:size>"
        '</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>'
    )
    file_ = (
        "<d:response><d:href>/remote.php/dav/files/mmazo/a%20b.txt</d:href>"
        "<d:propstat><d:prop>"
        "<d:getcontentlength>7</d:getcontentlength><d:getcontenttype>text/plain</d:getcontenttype>"
        '<d:resourcetype/><d:getetag>"xyz"</d:getetag>'
        "<d:getlastmodified>Wed, 24 Jun 2026 09:15:00 GMT</d:getlastmodified>"
        "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
        # propstat 404: props ausentes que NO deben leerse.
        "<d:propstat><d:prop><oc:size/></d:prop>"
        "<d:status>HTTP/1.1 404 Not Found</d:status></d:propstat></d:response>"
    )

    entries = parse_directory(_ms(_SELF, folder, file_), base_prefix=_BASE)

    by_name = {e.name: e for e in entries}
    assert by_name["Sub"].is_dir is True
    assert by_name["Sub"].size == 4096  # oc:size
    a_b = by_name["a b.txt"]  # href %20 decodificado
    assert a_b.is_dir is False
    assert a_b.size == 7 and a_b.mime == "text/plain"
    assert a_b.etag == "xyz"  # sin comillas
    assert a_b.modified is not None and a_b.modified.tzinfo is not None
    assert a_b.path == "/a b.txt"

"""Value objects y parseo WebDAV de Files (dominio puro, stdlib, sin I/O) — Bloque 2.4.

El adapter WebDAV (infra) hace el I/O contra Nextcloud Files
(``/remote.php/dav/files/<uid>/...``) y **delega aquí** la transformación del multistatus
a :class:`FileEntry`. Mismo **estilo** que ``domain.caldav`` (``xml.etree`` + namespaces),
pero SIN importarlo (Files y Calendar son superficies distintas). Solo stdlib, así que se
testea sin red.

ALCANCE (2.4): SOLO lectura (listar/leer). La **escritura** de Files queda para el Bloque
**2.4b** y comparte el gate de validación de escritura impersonada (Track A).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import unquote

_DAV = "{DAV:}"
_OC = "{http://owncloud.org/ns}"

# PROPFIND (Depth: 1) para listar una carpeta del usuario con metadatos básicos.
PROPFIND_FILES_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
    "<d:prop>"
    "<d:displayname/><d:getlastmodified/><d:getcontentlength/>"
    "<d:getcontenttype/><d:getetag/><d:resourcetype/><oc:size/>"
    "</d:prop>"
    "</d:propfind>"
)


@dataclass(frozen=True)
class FileEntry:
    """Una entrada de carpeta ya normalizada desde el multistatus WebDAV.

    * ``name``     — último segmento de la ruta (nombre del archivo/carpeta).
    * ``path``     — ruta **relativa al usuario**, sin barra final (p. ej.
      ``/Documentos/informe.txt``); es la que consume ``read_text_file``.
    * ``is_dir``   — ``True`` si es una colección (carpeta).
    * ``size``     — bytes (``getcontentlength`` de archivos; ``oc:size`` recursivo de
      carpetas), o ``None`` si no vino.
    * ``modified`` — ``getlastmodified`` parseado a datetime **aware**, o ``None``.
    * ``mime``     — ``getcontenttype`` (vacío/carpeta ⇒ ``None``).
    * ``etag``     — ``getetag`` sin comillas, o ``None``.
    """

    name: str
    path: str
    is_dir: bool
    size: int | None = None
    modified: datetime | None = None
    mime: str | None = None
    etag: str | None = None


def parse_directory(multistatus_xml: str, *, base_prefix: str) -> list[FileEntry]:
    """Multistatus de un PROPFIND Depth 1 → ``list[FileEntry]`` (excluye la carpeta misma).

    ``base_prefix`` es el prefijo DAV del usuario a recortar del ``href`` para dejar la
    ruta relativa (p. ej. ``/remote.php/dav/files/mmazo``). La entrada cuyo href coincide
    con la carpeta consultada (``self``) se omite, igual que ``listdir(exclude_self=True)``.
    """
    root = ET.fromstring(multistatus_xml)
    entries: list[FileEntry] = []
    for response in root.findall(f"{_DAV}response"):
        href_el = response.find(f"{_DAV}href")
        if href_el is None or not href_el.text:
            continue
        href = unquote(href_el.text.strip())
        rel = href[len(base_prefix):] if href.startswith(base_prefix) else href
        rel = "/" + rel.lstrip("/")  # normaliza a ruta absoluta-de-usuario
        path = rel.rstrip("/") or "/"
        if path == "/":  # la carpeta consultada (self): se excluye
            continue

        prop = _ok_prop(response)
        if prop is None:
            continue
        is_dir = _is_collection(prop)
        entries.append(
            FileEntry(
                name=path.rsplit("/", 1)[-1],
                path=path,
                is_dir=is_dir,
                size=_size(prop, is_dir),
                modified=_modified(prop),
                mime=_text(prop, f"{_DAV}getcontenttype"),
                etag=_etag(prop),
            )
        )
    return entries


def _ok_prop(response: ET.Element) -> ET.Element | None:
    """Devuelve el ``<d:prop>`` del ``propstat`` con status 200 (ignora los 404 de props ausentes)."""
    for propstat in response.findall(f"{_DAV}propstat"):
        status = propstat.find(f"{_DAV}status")
        if status is not None and status.text and "200" in status.text:
            return propstat.find(f"{_DAV}prop")
    return None


def _is_collection(prop: ET.Element) -> bool:
    rtype = prop.find(f"{_DAV}resourcetype")
    return rtype is not None and rtype.find(f"{_DAV}collection") is not None


def _size(prop: ET.Element, is_dir: bool) -> int | None:
    """``getcontentlength`` (archivos) o ``oc:size`` (carpetas, tamaño recursivo)."""
    raw = _text(prop, f"{_DAV}getcontentlength")
    if raw is None and is_dir:
        raw = _text(prop, f"{_OC}size")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _modified(prop: ET.Element) -> datetime | None:
    raw = _text(prop, f"{_DAV}getlastmodified")
    if raw is None:
        return None
    try:
        return parsedate_to_datetime(raw)  # HTTP-date (RFC 1123) → datetime aware
    except (TypeError, ValueError):
        return None


def _etag(prop: ET.Element) -> str | None:
    raw = _text(prop, f"{_DAV}getetag")
    return raw.strip('"') if raw else None


def _text(prop: ET.Element, tag: str) -> str | None:
    el = prop.find(tag)
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None

"""Adapter de Files (WebDAV) sobre Nextcloud — implementa `FilesPort` (SOLO lectura).

Mismo patrón que los adapters de Calendar/Deck (ADR-016): encapsula su **propio** cliente
HTTP firmado que replica la auth de AppAPI
(``AUTHORIZATION-APP-API: base64(uid:app_secret)``) para **impersonar** al usuario, SIN
tocar el adaptador privado ``nc._session.adapter`` (deuda **D-IMP-1**) ni importar nada de
``app/_spike``. El ``app_secret`` **NUNCA** se loguea.

El spike de ADR-006 accedía a Files vía ``nc_py_api`` (``set_user`` + ``files.listdir`` /
``download2stream``); aquí, por D-IMP-1, se habla **WebDAV crudo** con el cliente propio:
  * ``PROPFIND`` Depth 1 a ``/remote.php/dav/files/<uid>/<path>`` → listado (207).
  * ``GET`` de ``/remote.php/dav/files/<uid>/<path>`` → contenido (200).
El parseo del multistatus se **delega** a ``domain.files`` (puro, testeable). ``read_text_file``
**streamea** y corta a un límite de tamaño configurable, y rechaza binarios (byte nulo o no
UTF-8) — para no volcar un archivo enorme ni binario al contexto del LLM.

Status crudo como **dato**: 207/200 ok; 403/404 (o exceso/binario) ⇒ `FilesError` con
mensaje claro (la skill lo traduce a ``SkillResult.failure``; nunca tumba el loop).

ESCRITURA fuera de alcance (Bloque **2.4b**, comparte gate con la validación de escritura
impersonada del Track A). ``transport`` es inyectable para tests sin red.
"""
from __future__ import annotations

import base64
import logging
from urllib.parse import quote

import httpx

from app.domain.files import PROPFIND_FILES_BODY, FileEntry, parse_directory

logger = logging.getLogger(__name__)

_MULTISTATUS = 207
_XML_CONTENT_TYPE = "application/xml; charset=utf-8"
_DEFAULT_MAX_TEXT_BYTES = 256 * 1024  # 256 KB


class FilesError(Exception):
    """Fallo del adapter de Files (transporte, HTTP, exceso de tamaño, binario)."""


class NextcloudFilesAdapter:
    """Implementa `FilesPort` contra WebDAV de Nextcloud, impersonando al usuario."""

    def __init__(
        self,
        *,
        endpoint: str,
        app_id: str,
        app_version: str,
        app_secret: str,
        aa_version: str = "2.2.0",
        dav_url_suffix: str = "remote.php/dav",
        max_text_bytes: int = _DEFAULT_MAX_TEXT_BYTES,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not endpoint or not app_id or not app_secret:
            raise FilesError(
                "NEXTCLOUD_URL, APP_ID y APP_SECRET son obligatorios para el "
                "adapter de Files impersonado."
            )
        self._endpoint = endpoint.removesuffix("/index.php").rstrip("/")
        self._dav_suffix = "/" + dav_url_suffix.strip("/")
        self._app_id = app_id
        self._app_version = app_version
        self._app_secret = app_secret  # NUNCA se loguea
        self._aa_version = aa_version
        self._max_text_bytes = max_text_bytes
        self._timeout_s = timeout_s
        self._transport = transport

    async def list_files(self, uid: str, path: str = "/") -> list[FileEntry]:
        if not uid:
            raise FilesError("uid vacío: no hay identidad que impersonar.")
        norm = _norm_path(path)
        url = self._dav_url(uid, norm)
        async with self._client() as client:
            resp = await client.request(
                "PROPFIND",
                url,
                headers={
                    **self._headers(uid),
                    "Depth": "1",
                    "Content-Type": _XML_CONTENT_TYPE,
                },
                content=PROPFIND_FILES_BODY,
            )
        if resp.status_code != _MULTISTATUS:
            raise FilesError(_read_error(resp.status_code, norm, uid, "listar"))

        base_prefix = f"{self._dav_suffix}/files/{uid}"
        entries = parse_directory(resp.text, base_prefix=base_prefix)
        # Carpetas primero, luego por nombre (case-insensitive) — presentación estable.
        entries.sort(key=lambda e: (not e.is_dir, e.name.casefold()))
        logger.info("Archivos listados para %s en %r: %d.", uid, norm, len(entries))
        return entries

    async def read_text_file(self, uid: str, path: str) -> str:
        if not uid:
            raise FilesError("uid vacío: no hay identidad que impersonar.")
        norm = _norm_path(path)
        url = self._dav_url(uid, norm)

        data = bytearray()
        async with self._client() as client:
            async with client.stream("GET", url, headers=self._headers(uid)) as resp:
                if resp.status_code != 200:
                    raise FilesError(_read_error(resp.status_code, norm, uid, "leer"))
                declared = resp.headers.get("Content-Length")
                if (
                    declared is not None
                    and declared.isdigit()
                    and int(declared) > self._max_text_bytes
                ):
                    raise FilesError(_too_large(norm, self._max_text_bytes))
                async for chunk in resp.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > self._max_text_bytes:
                        raise FilesError(_too_large(norm, self._max_text_bytes))

        if b"\x00" in data:
            raise FilesError(f"El archivo {norm!r} no es de texto (parece binario).")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FilesError(
                f"El archivo {norm!r} no es texto legible (no es UTF-8)."
            ) from exc

    # --- infraestructura interna --------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._endpoint,
            timeout=self._timeout_s,
            transport=self._transport,
        )

    def _dav_url(self, uid: str, norm_path: str) -> str:
        return f"{self._dav_suffix}/files/{quote(uid)}{quote(norm_path, safe='/')}"

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


def _norm_path(path: str) -> str:
    """Normaliza a ruta absoluta-de-usuario con barra inicial (``/`` = raíz del usuario)."""
    return "/" + str(path or "").strip().lstrip("/")


def _too_large(path: str, limit: int) -> str:
    return (
        f"El archivo {path!r} supera el límite de lectura de {limit} bytes "
        f"({limit // 1024} KB); no lo leo para no saturar la respuesta."
    )


def _read_error(status: int, path: str, uid: str, action: str) -> str:
    """Mensaje claro para los rechazos esperables de lectura (Bloque 2.4)."""
    if status == 404:
        return f"No encontré {path!r} en tus archivos (HTTP 404)."
    if status == 403:
        return f"No tienes permiso para {action} {path!r} (HTTP 403)."
    return f"No se pudo {action} {path!r}: HTTP {status} (uid={uid!r})."

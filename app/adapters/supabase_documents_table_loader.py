"""Lector del corpus desde la tabla-catálogo `documents` (Supabase PostgREST).

ADR-006-ter: la fuente de verdad del corpus dejó de ser un bucket (ADR-006-bis,
`SupabaseStorageLoader`, OBSOLETO) y pasó a una TABLA-catálogo `documents` en el
proyecto de ``SUPABASE_URL``. Cada fila describe un documento; su columna ``url``
apunta al PDF/archivo público alojado en OTRO proyecto Supabase (dominio
distinto), descargable SIN autenticación. Esto acopla la ingestión a 2 proyectos
(deuda registrada en ARCHITECTURE.md §7).

Implementa `CorpusLoaderPort`, READ-ONLY (anon key, solo lectura):
- ``list_documents``: ``GET {SUPABASE_URL}/rest/v1/documents?select=*`` paginado;
  filtra por ``access_level`` (env ``RAG_INGEST_LEVELS``) y descarta entradas
  inservibles (sin ``url``, placeholders de carpeta vacía).
- ``download``: ``httpx.GET`` directo a la ``url`` pública (SIN headers de auth:
  es otro proyecto), con timeout y reintentos con backoff ante timeouts.

La extracción de texto NO vive aquí (la hace `TextExtractorPort` en el servicio,
por las reglas de capas): este adapter entrega bytes crudos.

Distinción de credenciales importante:
- La lectura de la TABLA usa la anon key de SUPABASE (header ``apikey``).
- La descarga del ARCHIVO NO lleva esa key (es público y de otro proyecto);
  mandar la apikey ahí sería, además de inútil, una fuga de credencial cross-host.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

import httpx

from app.services.corpus_loader_port import StoredObject

logger = logging.getLogger(__name__)

# Página de PostgREST. La tabla tiene ~26 filas; paginamos por robustez ante
# crecimiento. PostgREST tope-a en `max-rows` del servidor, así que iteramos.
_TABLE_PAGE_SIZE = 1000

# Nombres tratados como placeholders de carpeta vacía (bug observado en prod).
_PLACEHOLDER_NAMES = {".emptyfolderplaceholder"}

# Inferencia de MIME por extensión. El TextExtractorPort es la autoridad final
# sobre qué tipos sabe extraer; aquí solo etiquetamos lo mejor posible.
_EXT_TO_MIME = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".json": "application/json",
}


class DocumentsTableError(Exception):
    """Fallo al leer la tabla-catálogo `documents` vía PostgREST."""


def _infer_content_type(name: str) -> str:
    lower = name.lower()
    for ext, mime in _EXT_TO_MIME.items():
        if lower.endswith(ext):
            return mime
    return "application/octet-stream"


def _is_placeholder(name: str) -> bool:
    base = name.strip().lower().rsplit("/", 1)[-1]
    return base in _PLACEHOLDER_NAMES or base.startswith(".")


class SupabaseDocumentsTableLoader:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        levels: Iterable[str],
        timeout_s: float = 30.0,
        max_retries: int = 3,
        retry_backoff_s: float = 0.5,
        table: str = "documents",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url or not api_key:
            raise DocumentsTableError(
                "SUPABASE_URL y SUPABASE_KEY son obligatorios para leer la "
                "tabla-catálogo `documents`."
            )
        self._base = base_url.rstrip("/")
        self._table = table
        # Filtro case-insensitive; el role_scope se conserva LITERAL (sin aplanar).
        self._levels = {lvl.strip().lower() for lvl in levels if lvl.strip()}
        self._timeout_s = timeout_s
        self._max_retries = max(1, max_retries)
        self._retry_backoff_s = retry_backoff_s
        self._transport = transport
        # apikey SOLO para la tabla; jamás se reusa en la descarga del archivo.
        self._table_headers = {
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    async def list_documents(self) -> list[StoredObject]:
        rows = await self._fetch_rows()
        objects: list[StoredObject] = []
        skipped_placeholder = 0
        skipped_no_url = 0
        skipped_level = 0

        for row in rows:
            access_level = (row.get("access_level") or "").strip()
            if access_level.lower() not in self._levels:
                skipped_level += 1
                continue

            name = (row.get("name") or "").strip()
            url = (row.get("url") or "").strip()
            if not url:
                skipped_no_url += 1
                logger.info("Fila sin url ignorada (id=%s, name=%r).", row.get("id"), name)
                continue
            if _is_placeholder(name):
                skipped_placeholder += 1
                logger.info("Placeholder ignorado (id=%s, name=%r).", row.get("id"), name)
                continue

            objects.append(
                StoredObject(
                    path=url,  # locator de descarga
                    content_type=_infer_content_type(name),
                    size=0,  # la tabla no expone tamaño; desconocido
                    role_scope=access_level,  # LITERAL: noroot/root/semiroot
                    source=str(row.get("id")),  # cita estable (ADR-013)
                )
            )

        logger.info(
            "Catálogo: %d filas; %d incluidas; omitidas -> nivel:%d sin_url:%d placeholder:%d.",
            len(rows),
            len(objects),
            skipped_level,
            skipped_no_url,
            skipped_placeholder,
        )
        return objects

    async def download(self, path: str) -> bytes:
        """Descarga la URL pública `path`. SIN auth (otro proyecto). Reintenta timeouts."""
        last_exc: Exception | None = None
        async with httpx.AsyncClient(
            timeout=self._timeout_s, transport=self._transport, follow_redirects=True
        ) as client:
            for attempt in range(1, self._max_retries + 1):
                try:
                    resp = await client.get(path)  # sin headers de auth a propósito
                except httpx.TimeoutException as exc:
                    last_exc = exc
                    if attempt < self._max_retries:
                        backoff = self._retry_backoff_s * (2 ** (attempt - 1))
                        logger.warning(
                            "Timeout descargando %s (intento %d/%d); reintento en %.1fs.",
                            path, attempt, self._max_retries, backoff,
                        )
                        await asyncio.sleep(backoff)
                        continue
                    raise DocumentsTableError(
                        f"Descarga agotó {self._max_retries} reintentos por timeout: {path!r}"
                    ) from exc
                if resp.status_code != 200:
                    raise DocumentsTableError(
                        f"Descarga falló para {path!r}: HTTP {resp.status_code}"
                    )
                return resp.content
        # Inalcanzable: el bucle siempre retorna o lanza.
        raise DocumentsTableError(f"Descarga falló para {path!r}: {last_exc}")

    async def _fetch_rows(self) -> list[dict]:
        url = f"{self._base}/rest/v1/{self._table}"
        rows: list[dict] = []
        offset = 0
        async with httpx.AsyncClient(
            timeout=self._timeout_s, transport=self._transport
        ) as client:
            while True:
                params = {
                    "select": "*",
                    "limit": str(_TABLE_PAGE_SIZE),
                    "offset": str(offset),
                    "order": "id",
                }
                resp = await client.get(url, headers=self._table_headers, params=params)
                if resp.status_code != 200:
                    raise DocumentsTableError(
                        f"Lectura de tabla {self._table!r} falló: HTTP "
                        f"{resp.status_code} {resp.text}"
                    )
                page = resp.json()
                if not isinstance(page, list):
                    raise DocumentsTableError(
                        f"Respuesta inesperada de PostgREST (no es lista): {page!r}"
                    )
                rows.extend(page)
                if len(page) < _TABLE_PAGE_SIZE:
                    break
                offset += _TABLE_PAGE_SIZE
        return rows

"""Lector del corpus en Supabase Storage vía REST (httpx), READ-ONLY.

ADR-006-bis: el corpus físico vive en Supabase Storage y se consume en modo
solo-lectura. Este adapter NO escribe: solo lista y descarga objetos.

Implementa `CorpusLoaderPort`. Se eligió httpx directo sobre el SDK oficial
`supabase` para no arrastrar sus dependencias pesadas (gotrue/storage3/postgrest)
ni sus pins; httpx ya entra como dependencia transitiva del stack.

Seguridad: usar una credencial de MÍNIMO PRIVILEGIO (clave read-only del bucket),
nunca la service-role key que salta RLS.
"""
from __future__ import annotations

import logging

import httpx

from app.services.corpus_loader_port import StoredObject

logger = logging.getLogger(__name__)

_LIST_PAGE_SIZE = 100


class StorageError(Exception):
    """Fallo al hablar con Supabase Storage."""


class SupabaseStorageLoader:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        bucket: str,
        prefix: str = "",
        timeout_s: float = 30.0,
    ) -> None:
        if not base_url or not api_key or not bucket:
            raise StorageError(
                "SUPABASE_URL, SUPABASE_KEY y SUPABASE_BUCKET son obligatorios "
                "para la ingestión."
            )
        self._base = base_url.rstrip("/")
        self._bucket = bucket
        self._prefix = prefix.strip().strip("/")
        self._timeout_s = timeout_s
        self._headers = {
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
        }

    async def list_documents(self) -> list[StoredObject]:
        """Lista recursivamente los objetos (archivos) bajo `prefix`."""
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            return await self._list_recursive(client, self._prefix)

    async def download(self, path: str) -> bytes:
        url = f"{self._base}/storage/v1/object/{self._bucket}/{path}"
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.get(url, headers=self._headers)
        if resp.status_code != 200:
            raise StorageError(
                f"Descarga falló para {path!r}: HTTP {resp.status_code} {resp.text}"
            )
        return resp.content

    async def _list_recursive(
        self, client: httpx.AsyncClient, prefix: str
    ) -> list[StoredObject]:
        objects: list[StoredObject] = []
        offset = 0
        while True:
            page = await self._list_page(client, prefix, offset)
            if not page:
                break
            for entry in page:
                name = entry.get("name")
                if not name:
                    continue
                full = f"{prefix}/{name}" if prefix else name
                metadata = entry.get("metadata")
                if metadata is None:
                    # Entrada sin metadata = "carpeta": recurse.
                    objects.extend(await self._list_recursive(client, full))
                else:
                    objects.append(
                        StoredObject(
                            path=full,
                            content_type=metadata.get("mimetype", "application/octet-stream"),
                            size=int(metadata.get("size", 0)),
                        )
                    )
            if len(page) < _LIST_PAGE_SIZE:
                break
            offset += _LIST_PAGE_SIZE
        return objects

    async def _list_page(
        self, client: httpx.AsyncClient, prefix: str, offset: int
    ) -> list[dict]:
        url = f"{self._base}/storage/v1/object/list/{self._bucket}"
        body = {
            "prefix": prefix,
            "limit": _LIST_PAGE_SIZE,
            "offset": offset,
            "sortBy": {"column": "name", "order": "asc"},
        }
        resp = await client.post(url, headers=self._headers, json=body)
        if resp.status_code != 200:
            raise StorageError(
                f"Listado falló (prefix={prefix!r}): HTTP {resp.status_code} {resp.text}"
            )
        return resp.json()

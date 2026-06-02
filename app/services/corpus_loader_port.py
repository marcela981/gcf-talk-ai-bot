"""Puerto para leer el corpus físico (Supabase Storage, read-only — ADR-006-bis).

Implementación en adapters/supabase_storage_loader.py. El DTO `StoredObject` es
deliberadamente mínimo (metadatos del listado) para no acoplar los servicios al
formato de respuesta del proveedor de almacenamiento.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    """Metadatos de un objeto del bucket. `path` es la ruta dentro del bucket."""

    path: str
    content_type: str
    size: int


class CorpusLoaderPort(Protocol):
    async def list_documents(self) -> list[StoredObject]:
        """Lista los objetos del corpus (solo metadatos)."""
        ...

    async def download(self, path: str) -> bytes:
        """Descarga el contenido binario de un objeto."""
        ...

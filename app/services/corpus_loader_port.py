"""Puerto para leer el corpus físico (read-only).

Implementaciones:
- adapters/supabase_storage_loader.py — bucket de Supabase Storage (ADR-006-bis,
  OBSOLETO; ver ADR-006-ter).
- adapters/supabase_documents_table_loader.py — tabla-catálogo `documents`
  (ADR-006-ter, fuente vigente).

El DTO `StoredObject` es deliberadamente mínimo (metadatos del listado) para no
acoplar los servicios al formato de respuesta del proveedor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    """Metadatos de un documento del corpus.

    * ``path``         — *locator* opaco que `download()` sabe resolver. En el
      loader de bucket es la ruta dentro del bucket; en el loader de tabla es la
      URL pública del documento (ADR-006-ter).
    * ``content_type`` — MIME inferido del documento; el ``TextExtractorPort``
      decide qué tipos sabe extraer.
    * ``size``         — tamaño en bytes si la fuente lo expone; ``0`` = desconocido
      (la tabla-catálogo no lo publica). No se usa como criterio de filtrado.
    * ``role_scope``   — scope de rol literal de ESTE documento (p. ej.
      ``"noroot"``). ``None`` ⇒ el servicio aplica su scope por defecto. Permite
      scope por-documento sin romper a quien no lo informe (loader de bucket).
    * ``source``       — identidad estable usada como cita del chunk (ADR-013).
      ``None`` ⇒ el servicio usa ``path``.
    """

    path: str
    content_type: str
    size: int
    role_scope: str | None = None
    source: str | None = None


class CorpusLoaderPort(Protocol):
    async def list_documents(self) -> list[StoredObject]:
        """Lista los objetos del corpus (solo metadatos)."""
        ...

    async def download(self, path: str) -> bytes:
        """Descarga el contenido binario de un objeto."""
        ...

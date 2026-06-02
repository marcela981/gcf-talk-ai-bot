"""Puertos del vector store. Implementaciones en adapters/pgvector_store.py.

Se separan lectura (`RetrievalPort`) y escritura (`VectorWritePort`) por ISP: la
ruta de petición del bot solo necesita leer; la CLI de ingestión solo necesita
escribir. El adapter de pgvector implementa ambos, pero cada servicio depende
únicamente del puerto que usa.
"""
from __future__ import annotations

from typing import Protocol

from app.domain.chunk import Chunk, EmbeddedChunk


class RetrievalPort(Protocol):
    async def search(
        self,
        query_embedding: list[float],
        role_scope: str,
    ) -> list[Chunk]:
        """Búsqueda por similitud filtrada por rol.

        `role_scope` se aplica como FILTRO PRE-retrieval en la query (WHERE),
        nunca después (antipatrón ACL post-retrieval). El umbral de similitud y
        el top_k los fija la implementación al construirse (derivados de
        `RetrievalPolicy`). Los `Chunk` devueltos traen `score` poblado.
        """
        ...


class VectorWritePort(Protocol):
    async def upsert(self, chunks: list[EmbeddedChunk]) -> int:
        """Inserta/actualiza fragmentos y devuelve cuántas filas escribió.

        Idempotente por documento: reemplaza todos los fragmentos de cada
        `source` presente en el lote (DELETE + INSERT transaccional) para que
        reindexar no duplique ni deje fragmentos huérfanos.
        """
        ...

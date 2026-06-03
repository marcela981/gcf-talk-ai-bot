"""Adapter de pgvector: implementa RetrievalPort (search) y VectorWritePort (upsert).

ADR-007: vector store dedicado en Postgres + pgvector, externo al de Chat IA.

Stateless por petición: abre una conexión por operación (sin estado global
mutable compartido entre usuarios). El umbral de similitud y el top_k se fijan al
construir el adapter (derivados de `RetrievalPolicy`), de modo que la firma del
puerto `search(query_embedding, role_scope)` permanece estable.

Distancia coseno: la similitud reportada es `1 - (embedding <=> q)`, en [0, 1].
El filtro por rol (`role_scope`) va en el WHERE (PRE-retrieval), nunca después.

Tipo `vector` (por qué upsert funcionaba y search no):
`register_vector_async` registra dumpers SOLO para `numpy.ndarray` y la clase
`Vector` de pgvector — NO para `list`. Si se bindea una `list[float]`, psycopg la
envía como `double precision[]`. En `upsert` el INSERT escribe en una columna
`vector` y pgvector aplica su cast de ASIGNACIÓN `double precision[] -> vector`,
así que toleraba el array. En `search` el operador `<=>` exige `vector <=> vector`
y en contexto de operador NO se aplican casts de asignación: de ahí el error
`operator does not exist: vector <=> double precision[]`. La solución unificada es
envolver SIEMPRE el embedding en `Vector` (ver `_to_vector`), un único punto que
deja ambos caminos enviando el tipo `vector` nativo.

Privilegio mínimo: el bot solo lee (search); idealmente el DSN del bot apunta a
un rol Postgres de solo-lectura y la ingestión usa un DSN con escritura.
PENDIENTE: separar PGVECTOR_DSN_READONLY (bot) de PGVECTOR_DSN (ingest) — el spec
expone un único PGVECTOR_DSN; documentado como endurecimiento para el operador.
"""
from __future__ import annotations

import logging

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector_async

from app.domain.chunk import Chunk, EmbeddedChunk

logger = logging.getLogger(__name__)

_TABLE = "rag_chunks"


def _to_vector(values: list[float]) -> Vector:
    """Envuelve un embedding en el tipo `vector` nativo de pgvector.

    Único punto de conversión: `register_vector_async` solo conoce `numpy.ndarray`
    y `Vector`, no `list`. Pasar la `list` cruda la bindearía como
    `double precision[]` (rompe el operador `<=>` en search; ver docstring del
    módulo). Usar `Vector` evita depender de numpy en el call site y hace que
    search (operador) y upsert (INSERT) usen el mismo tipo.
    """
    return Vector(values)

_SEARCH_SQL = f"""
SELECT source, chunk_id, content, role_scope, 1 - (embedding <=> %(q)s) AS score
FROM {_TABLE}
WHERE role_scope = %(role)s
  AND 1 - (embedding <=> %(q)s) >= %(threshold)s
ORDER BY embedding <=> %(q)s
LIMIT %(top_k)s
"""


class PgVectorStore:
    def __init__(
        self,
        *,
        dsn: str,
        top_k: int,
        similarity_threshold: float,
    ) -> None:
        self._dsn = dsn
        self._top_k = top_k
        self._threshold = similarity_threshold

    async def search(
        self,
        query_embedding: list[float],
        role_scope: str,
    ) -> list[Chunk]:
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            await register_vector_async(conn)
            async with conn.cursor() as cur:
                await cur.execute(
                    _SEARCH_SQL,
                    {
                        "q": _to_vector(query_embedding),
                        "role": role_scope,
                        "threshold": self._threshold,
                        "top_k": self._top_k,
                    },
                )
                rows = await cur.fetchall()
        return [
            Chunk(
                source=row[0],
                chunk_id=row[1],
                content=row[2],
                role_scope=row[3],
                score=float(row[4]),
            )
            for row in rows
        ]

    async def upsert(self, chunks: list[EmbeddedChunk]) -> int:
        if not chunks:
            return 0
        sources = sorted({ec.chunk.source for ec in chunks})
        rows = [
            (
                ec.chunk.source,
                ec.chunk.chunk_id,
                ec.chunk.role_scope,
                ec.chunk.content,
                _to_vector(ec.embedding),
            )
            for ec in chunks
        ]
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            await register_vector_async(conn)
            async with conn.cursor() as cur:
                # Idempotencia por documento: borra los fragmentos previos de
                # cada source presente en el lote y reinserta. Todo en una
                # transacción (autocommit off por defecto en psycopg3).
                await cur.execute(
                    f"DELETE FROM {_TABLE} WHERE source = ANY(%s)", (sources,)
                )
                await cur.executemany(
                    f"INSERT INTO {_TABLE} "
                    "(source, chunk_id, role_scope, content, embedding) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    rows,
                )
            await conn.commit()
        return len(rows)

"""Tests del PgVectorStore sin red: verifican que el embedding se bindea como el
tipo `vector` nativo de pgvector (Vector), no como list[float] (que psycopg
mandaría como double precision[] y rompería el operador `<=>`).

Se reemplazan `psycopg` y `register_vector_async` por dobles que capturan los
parámetros y el orden de las llamadas, sin abrir conexión real.
"""
from __future__ import annotations

import pytest
from pgvector import Vector

from app.adapters import pgvector_store
from app.adapters.pgvector_store import PgVectorStore
from app.domain.chunk import Chunk, EmbeddedChunk


class FakeCursor:
    def __init__(self, events: dict) -> None:
        self._events = events

    async def __aenter__(self) -> "FakeCursor":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def execute(self, sql, params=None) -> None:
        self._events["order"].append("execute")
        self._events.setdefault("executes", []).append((sql, params))

    async def executemany(self, sql, rows) -> None:
        self._events["order"].append("executemany")
        self._events.setdefault("executemany", []).append((sql, list(rows)))

    async def fetchall(self):
        return self._events.get("rows", [])


class FakeConn:
    def __init__(self, events: dict) -> None:
        self._events = events

    async def __aenter__(self) -> "FakeConn":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._events)

    async def commit(self) -> None:
        self._events["order"].append("commit")


@pytest.fixture
def patched(monkeypatch):
    """Parchea psycopg.connect y register_vector_async; devuelve el dict de eventos."""
    events: dict = {"order": []}

    class _FakePsycopg:
        class AsyncConnection:
            @staticmethod
            async def connect(dsn):
                events["dsn"] = dsn
                return FakeConn(events)

    async def _fake_register(conn) -> None:
        events["order"].append("register")

    monkeypatch.setattr(pgvector_store, "psycopg", _FakePsycopg)
    monkeypatch.setattr(pgvector_store, "register_vector_async", _fake_register)
    return events


@pytest.mark.asyncio
async def test_search_binds_query_embedding_as_vector(patched):
    store = PgVectorStore(dsn="postgresql://x", top_k=4, similarity_threshold=0.75)

    await store.search([0.1, 0.2, 0.3], role_scope="noroot")

    sql, params = patched["executes"][0]
    # El parámetro de la query NO debe ser una list cruda (-> double precision[]),
    # sino un Vector nativo (-> tipo vector, aceptado por `<=>`).
    assert isinstance(params["q"], Vector)
    assert not isinstance(params["q"], list)
    assert params["q"].to_list() == pytest.approx([0.1, 0.2, 0.3], abs=1e-6)
    assert "<=>" in sql  # sigue usando el operador de distancia coseno


@pytest.mark.asyncio
async def test_search_registers_vector_type_before_executing(patched):
    store = PgVectorStore(dsn="postgresql://x", top_k=4, similarity_threshold=0.75)

    await store.search([0.1, 0.2, 0.3], role_scope="noroot")

    # El registro del tipo debe ocurrir ANTES del execute (si no, el bind falla).
    assert patched["order"].index("register") < patched["order"].index("execute")


@pytest.mark.asyncio
async def test_upsert_binds_embedding_as_vector(patched):
    store = PgVectorStore(dsn="postgresql://x", top_k=4, similarity_threshold=0.75)
    chunks = [
        EmbeddedChunk(
            chunk=Chunk(source="10", chunk_id=0, content="hola", role_scope="noroot"),
            embedding=[0.4, 0.5, 0.6],
        )
    ]

    written = await store.upsert(chunks)

    assert written == 1
    _, rows = patched["executemany"][0]
    embedding_param = rows[0][4]  # (source, chunk_id, role_scope, content, embedding)
    # upsert ahora también envía Vector (antes funcionaba por el cast de
    # asignación array->vector del INSERT; ahora es consistente con search).
    assert isinstance(embedding_param, Vector)
    assert embedding_param.to_list() == pytest.approx([0.4, 0.5, 0.6], abs=1e-6)


@pytest.mark.asyncio
async def test_upsert_registers_vector_type_before_executing(patched):
    store = PgVectorStore(dsn="postgresql://x", top_k=4, similarity_threshold=0.75)
    chunks = [
        EmbeddedChunk(
            chunk=Chunk(source="10", chunk_id=0, content="hola", role_scope="noroot"),
            embedding=[0.4, 0.5, 0.6],
        )
    ]

    await store.upsert(chunks)

    order = patched["order"]
    assert order.index("register") < order.index("executemany")
    assert order[-1] == "commit"  # commit al final de la transacción

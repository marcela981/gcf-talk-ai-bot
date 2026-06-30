"""Unit tests for app.adapters.knowledge_base_skill (Skill #1, ADR-018).

El embedder y el vector store se reemplazan por dobles — sin red. Se verifica que
la skill: embebe la consulta, filtra por la política, devuelve fragmentos citables,
maneja el caso sin resultados, valida el argumento y convierte fallos de I/O en un
`SkillResult.failure` (dato, no excepción).
"""
from __future__ import annotations

import json

import pytest

from app.adapters.knowledge_base_skill import KnowledgeBaseSkill
from app.domain.actor_context import ActorContext
from app.domain.chunk import Chunk
from app.domain.retrieval_policy import RetrievalPolicy


ACTOR = ActorContext(actor_id="users/alice", token="room1")


class FakeEmbedder:
    def __init__(self, vector: list[float] | None = None) -> None:
        self._vector = vector or [0.1, 0.2, 0.3]
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [self._vector for _ in texts]


class FakeRetrieval:
    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks
        self.calls: list[tuple[list[float], str]] = []

    async def search(self, query_embedding: list[float], role_scope: str) -> list[Chunk]:
        self.calls.append((query_embedding, role_scope))
        return list(self._chunks)


class BoomEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedder caído")


def _policy() -> RetrievalPolicy:
    return RetrievalPolicy(top_k=4, similarity_threshold=0.75)


def _skill(embedder, retrieval, *, role_scope: str = "corporate") -> KnowledgeBaseSkill:
    return KnowledgeBaseSkill(
        embedder=embedder,
        retrieval=retrieval,
        retrieval_policy=_policy(),
        role_scope=role_scope,
    )


@pytest.mark.asyncio
async def test_returns_relevant_fragments_with_their_source():
    chunks = [
        Chunk(
            source="politicas-rrhh.pdf",
            chunk_id=0,
            content="Las vacaciones anuales son de 15 días hábiles.",
            role_scope="corporate",
            score=0.91,
        )
    ]
    embedder = FakeEmbedder()
    retrieval = FakeRetrieval(chunks)
    skill = _skill(embedder, retrieval)

    result = await skill.execute({"consulta": "¿cuántos días de vacaciones?"}, ACTOR)

    assert result.ok
    fragmentos = result.data["fragmentos"]
    assert len(fragmentos) == 1
    assert fragmentos[0]["fuente"] == "politicas-rrhh.pdf"
    assert fragmentos[0]["contenido"].startswith("Las vacaciones")
    # Se embebe la consulta y se busca con el scope fijo de la skill.
    assert embedder.calls == [["¿cuántos días de vacaciones?"]]
    assert retrieval.calls[0][1] == "corporate"


@pytest.mark.asyncio
async def test_low_similarity_chunks_are_filtered_to_empty():
    chunks = [
        Chunk(
            source="x.md",
            chunk_id=0,
            content="irrelevante",
            role_scope="corporate",
            score=0.10,  # bajo el umbral 0.75
        )
    ]
    skill = _skill(FakeEmbedder(), FakeRetrieval(chunks))

    result = await skill.execute({"consulta": "algo"}, ACTOR)

    assert result.ok
    assert result.data["fragmentos"] == []
    assert "Sin resultados" in result.data["mensaje"]


@pytest.mark.asyncio
async def test_missing_or_empty_query_is_a_failure():
    skill = _skill(FakeEmbedder(), FakeRetrieval([]))

    missing = await skill.execute({}, ACTOR)
    blank = await skill.execute({"consulta": "   "}, ACTOR)

    assert not missing.ok and "consulta" in missing.error
    assert not blank.ok


@pytest.mark.asyncio
async def test_retrieval_error_becomes_failure_result_not_exception():
    skill = _skill(BoomEmbedder(), FakeRetrieval([]))

    result = await skill.execute({"consulta": "algo"}, ACTOR)

    assert not result.ok
    assert "base de conocimiento" in result.error


@pytest.mark.asyncio
async def test_configured_role_scope_is_used_app_only():
    # Aunque el ActorContext trae otra cosa, la skill usa SU scope fijo (app-only).
    actor = ActorContext(actor_id="users/bob", token="r", role_scope="root")
    retrieval = FakeRetrieval([])
    skill = _skill(FakeEmbedder(), retrieval, role_scope="corporate")

    await skill.execute({"consulta": "algo"}, actor)

    assert retrieval.calls[0][1] == "corporate"


def test_tool_content_round_trips_as_json():
    chunks = [
        Chunk(source="a.pdf", chunk_id=0, content="texto", role_scope="corporate", score=0.9)
    ]
    # Llamada directa al método de SkillResult para validar el contenido de la tool.
    from app.domain.skill_result import SkillResult

    rendered = SkillResult.success({"fragmentos": [{"fuente": "a.pdf"}]}).to_tool_content()
    parsed = json.loads(rendered)
    assert parsed["ok"] is True
    assert parsed["data"]["fragmentos"][0]["fuente"] == "a.pdf"
    assert chunks  # guarda que el fixture es válido

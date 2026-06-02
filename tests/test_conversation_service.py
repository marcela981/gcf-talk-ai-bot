"""Unit tests for app.services.conversation_service.

The LLM is replaced by a FakeLLM that satisfies the LLMPort protocol — no
real OpenAI SDK calls, no network.
"""
from __future__ import annotations

import pytest

from app.adapters.openai_adapter import LLMError
from app.domain.chunk import Chunk
from app.domain.message import Message
from app.domain.retrieval_policy import RetrievalPolicy
from app.services.conversation_service import ConversationService, _FALLBACK_MSG


MENTION = "IA"


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


class BoomRetrieval:
    async def search(self, query_embedding: list[float], role_scope: str) -> list[Chunk]:
        raise RuntimeError("vector store unavailable")


def _policy() -> RetrievalPolicy:
    return RetrievalPolicy(top_k=4, similarity_threshold=0.75)


def _system_blocks(messages: list[Message]) -> list[str]:
    return [m.content for m in messages if m.role == "system"]


class FakeLLM:
    def __init__(self, reply: str = "respuesta del fake") -> None:
        self._reply = reply
        self.calls: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
    ) -> str:
        self.calls.append(messages)
        return self._reply


class BoomLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
    ) -> str:
        self.calls += 1
        raise LLMError("simulated upstream failure")


@pytest.mark.asyncio
async def test_returns_none_and_does_not_call_llm_when_not_mentioned():
    fake = FakeLLM()
    service = ConversationService(llm=fake, bot_mention_name=MENTION)

    reply = await service.handle(
        raw_text="hola equipo",
        actor_id="users/alice",
        object_name="message",
    )

    assert reply is None
    assert fake.calls == []


@pytest.mark.asyncio
async def test_returns_llm_reply_and_strips_mention_from_prompt():
    fake = FakeLLM(reply="hola, ¿en qué te ayudo?")
    service = ConversationService(llm=fake, bot_mention_name=MENTION)

    reply = await service.handle(
        raw_text="@IA resume el último informe",
        actor_id="users/alice",
        object_name="message",
    )

    assert reply == "hola, ¿en qué te ayudo?"
    assert len(fake.calls) == 1
    sent = fake.calls[0]
    user_msg = next(m for m in sent if m.role == "user")
    assert user_msg.content == "resume el último informe"
    assert "@IA" not in user_msg.content
    assert "@ia" not in user_msg.content.lower()


@pytest.mark.asyncio
async def test_prefix_invocation_strips_prefix_in_prompt():
    fake = FakeLLM()
    service = ConversationService(llm=fake, bot_mention_name=MENTION)

    reply = await service.handle(
        raw_text="/ai ¿qué es SOLID?",
        actor_id="users/alice",
        object_name="message",
    )

    assert reply == "respuesta del fake"
    assert len(fake.calls) == 1
    sent = fake.calls[0]
    user_msg = next(m for m in sent if m.role == "user")
    assert user_msg.content == "¿qué es SOLID?"


@pytest.mark.asyncio
async def test_returns_fallback_on_llm_error():
    boom = BoomLLM()
    service = ConversationService(llm=boom, bot_mention_name=MENTION)

    reply = await service.handle(
        raw_text="@IA algo",
        actor_id="users/alice",
        object_name="message",
    )

    assert reply == _FALLBACK_MSG
    assert boom.calls == 1


# --- Fase 2: RAG -------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieved_context_injected_into_extra_system_with_citation():
    fake = FakeLLM(reply="ok")
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
    service = ConversationService(
        llm=fake,
        bot_mention_name=MENTION,
        embedder=embedder,
        retrieval=retrieval,
        retrieval_policy=_policy(),
        role_scope="corporate",
    )

    reply = await service.handle(
        raw_text="@IA ¿cuántos días de vacaciones tengo?",
        actor_id="users/alice",
        object_name="message",
    )

    assert reply == "ok"
    blocks = _system_blocks(fake.calls[0])
    assert len(blocks) == 2  # L0 + el bloque L2 recuperado
    l2 = blocks[-1]
    assert "politicas-rrhh.pdf" in l2  # cita la fuente (ADR-013)
    assert "Las vacaciones anuales son de 15 días hábiles." in l2
    # se embebe la consulta ya limpia y se busca con el scope de rol
    assert embedder.calls == [["¿cuántos días de vacaciones tengo?"]]
    assert retrieval.calls[0][1] == "corporate"


@pytest.mark.asyncio
async def test_low_similarity_chunks_are_filtered_out():
    fake = FakeLLM()
    chunks = [
        Chunk(
            source="x.md",
            chunk_id=0,
            content="contenido irrelevante",
            role_scope="corporate",
            score=0.10,  # bajo el umbral 0.75
        )
    ]
    service = ConversationService(
        llm=fake,
        bot_mention_name=MENTION,
        embedder=FakeEmbedder(),
        retrieval=FakeRetrieval(chunks),
        retrieval_policy=_policy(),
    )

    await service.handle(
        raw_text="@IA algo",
        actor_id="users/alice",
        object_name="message",
    )

    assert len(_system_blocks(fake.calls[0])) == 1  # solo L0, ningún fragmento pasa el umbral


@pytest.mark.asyncio
async def test_retrieval_failure_degrades_to_answer_without_context():
    fake = FakeLLM(reply="respondo igual")
    service = ConversationService(
        llm=fake,
        bot_mention_name=MENTION,
        embedder=FakeEmbedder(),
        retrieval=BoomRetrieval(),
        retrieval_policy=_policy(),
    )

    reply = await service.handle(
        raw_text="@IA algo",
        actor_id="users/alice",
        object_name="message",
    )

    assert reply == "respondo igual"  # el fallo de RAG no tumba la respuesta
    assert len(_system_blocks(fake.calls[0])) == 1  # solo L0, sin contexto


@pytest.mark.asyncio
async def test_without_rag_wiring_behaves_like_phase1():
    fake = FakeLLM()
    service = ConversationService(llm=fake, bot_mention_name=MENTION)

    await service.handle(
        raw_text="@IA hola",
        actor_id="users/alice",
        object_name="message",
    )

    assert len(_system_blocks(fake.calls[0])) == 1  # solo L0

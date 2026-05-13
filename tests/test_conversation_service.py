"""Unit tests for app.services.conversation_service.

The LLM is replaced by a FakeLLM that satisfies the LLMPort protocol — no
real OpenAI SDK calls, no network.
"""
from __future__ import annotations

import pytest

from app.adapters.openai_adapter import LLMError
from app.domain.message import Message
from app.services.conversation_service import ConversationService, _FALLBACK_MSG


BOT = "GCF AI Bot"


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
    service = ConversationService(llm=fake, bot_display_name=BOT)

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
    service = ConversationService(llm=fake, bot_display_name=BOT)

    reply = await service.handle(
        raw_text="@GCF AI Bot resume el último informe",
        actor_id="users/alice",
        object_name="message",
    )

    assert reply == "hola, ¿en qué te ayudo?"
    assert len(fake.calls) == 1
    sent = fake.calls[0]
    user_msg = next(m for m in sent if m.role == "user")
    assert user_msg.content == "resume el último informe"
    assert "@GCF AI Bot" not in user_msg.content
    assert "@gcf ai bot" not in user_msg.content.lower()


@pytest.mark.asyncio
async def test_returns_fallback_on_llm_error():
    boom = BoomLLM()
    service = ConversationService(llm=boom, bot_display_name=BOT)

    reply = await service.handle(
        raw_text="@GCF AI Bot algo",
        actor_id="users/alice",
        object_name="message",
    )

    assert reply == _FALLBACK_MSG
    assert boom.calls == 1

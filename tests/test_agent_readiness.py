"""Readiness del modo agente desacoplado de RAG (ADR-017 + decisión de cableado).

El motor activa el tool-use loop sii hay al menos UNA skill registrada, sea cual sea
su dependencia: la skill de calendario NO necesita RAG. Aquí se puebla el registry
con dobles (sin red, sin LLM real) para cubrir la matriz: solo KB, solo Calendar
(sin RAG), ambas, registry vacío (degrada a texto) y agente apagado (skills=None).

El gate por dependencia propia (rag_enabled→KB, appapi_ready→Calendar) vive en el
composition root (`app/main.py`); aquí se valida la consecuencia observable en el
servicio: registry no vacío ⇒ ruta de tool-calling; si no ⇒ ruta de texto Fase 1/2.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domain.actor_context import ActorContext
from app.domain.skill_result import SkillResult
from app.domain.tool_calling import LLMToolResponse
from app.services.conversation_service import ConversationService
from app.services.skill_registry import SkillRegistry

MENTION = "IA"


class _FakeSkill:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"skill {self._name}"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], actor: ActorContext) -> SkillResult:
        return SkillResult.success({})


class _DualLLM:
    def __init__(self) -> None:
        self.tool_calls = 0
        self.completes = 0

    async def chat_with_tools(self, messages, tools, *, model=None):
        self.tool_calls += 1
        return LLMToolResponse(text="respuesta del agente")

    async def complete(self, messages, *, model=None):
        self.completes += 1
        return "respuesta de texto"


def _registry(*names: str) -> SkillRegistry:
    registry = SkillRegistry()
    for name in names:
        registry.register(_FakeSkill(name))
    return registry


async def _handle(service: ConversationService) -> str | None:
    return await service.handle(
        raw_text="@IA ¿qué tengo hoy?",
        actor_id="users/alice",
        object_name="message",
        token="room1",
    )


@pytest.mark.parametrize(
    "names",
    [
        pytest.param(("consultar_base_conocimiento",), id="solo-KB"),
        pytest.param(("consultar_calendario",), id="solo-Calendar-sin-RAG"),
        pytest.param(
            ("consultar_base_conocimiento", "consultar_calendario"), id="ambas"
        ),
    ],
)
@pytest.mark.asyncio
async def test_agent_active_when_registry_has_any_skill(names):
    llm = _DualLLM()
    service = ConversationService(
        llm=llm, bot_mention_name=MENTION, skills=_registry(*names)
    )

    reply = await _handle(service)

    assert reply == "respuesta del agente"
    assert llm.tool_calls == 1 and llm.completes == 0


@pytest.mark.asyncio
async def test_empty_registry_degrades_to_text():
    llm = _DualLLM()
    service = ConversationService(
        llm=llm, bot_mention_name=MENTION, skills=SkillRegistry()
    )

    reply = await _handle(service)

    assert reply == "respuesta de texto"
    assert llm.completes == 1 and llm.tool_calls == 0


@pytest.mark.asyncio
async def test_agent_off_degrades_to_text():
    llm = _DualLLM()
    service = ConversationService(llm=llm, bot_mention_name=MENTION)  # skills=None

    reply = await _handle(service)

    assert reply == "respuesta de texto"
    assert llm.completes == 1 and llm.tool_calls == 0

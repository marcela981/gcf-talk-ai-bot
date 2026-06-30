"""Unit tests for the tool-use loop in ConversationService (ADR-017/ADR-018).

El LLM se reemplaza por un doble que emite tool-calls y luego texto — sin red,
sin SDK. Las skills son dobles que satisfacen el Protocol `Skill`. Se ejercita:
el ciclo completo (pide tool → ejecuta → reitera → texto final), la degradación
sin registry (ruta de texto puro `complete`), la resiliencia ante skills que
fallan o tools desconocidas, el tope de iteraciones y el registro en memoria.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.adapters.in_memory_conversation_memory import InMemoryConversationMemory
from app.adapters.openai_adapter import LLMError
from app.domain.actor_context import ActorContext
from app.domain.message import Message
from app.domain.skill_result import SkillResult
from app.domain.tool_calling import (
    AssistantToolCallTurn,
    LLMToolResponse,
    ToolCall,
    ToolResultTurn,
)
from app.services.conversation_service import (
    ConversationService,
    _AGENT_EXHAUSTED_MSG,
    _FALLBACK_MSG,
)
from app.services.skill_registry import SkillRegistry


MENTION = "IA"


class FakeSkill:
    def __init__(
        self,
        name: str = "fake_tool",
        *,
        result: SkillResult | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._name = name
        self._result = result or SkillResult.success({"valor": 42})
        self._raises = raises
        self.calls: list[tuple[dict[str, Any], ActorContext]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"skill {self._name}"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"q": {"type": "string"}}}

    async def execute(self, args: dict[str, Any], actor: ActorContext) -> SkillResult:
        self.calls.append((args, actor))
        if self._raises is not None:
            raise self._raises
        return self._result


class DualLLM:
    """Implementa ambas rutas del LLMPort para distinguir cuál usó el servicio.

    `chat_with_tools` devuelve respuestas en cola (una por iteración); `complete`
    devuelve un texto fijo. Cada llamada guarda una *copia* del transcript para
    poder inspeccionar su estado en esa iteración (el loop muta la lista in situ).
    """

    def __init__(
        self,
        *,
        tool_responses: list[LLMToolResponse] | None = None,
        text: str = "texto puro",
    ) -> None:
        self._tool_responses = list(tool_responses or [])
        self._text = text
        self.tool_calls_log: list[list[Any]] = []
        self.complete_calls: list[list[Message]] = []

    async def chat_with_tools(self, messages, tools, *, model=None):
        self.tool_calls_log.append(list(messages))
        return self._tool_responses.pop(0)

    async def complete(self, messages, *, model=None):
        self.complete_calls.append(list(messages))
        return self._text


def _registry(*skills) -> SkillRegistry:
    registry = SkillRegistry()
    for skill in skills:
        registry.register(skill)
    return registry


async def _handle(service: ConversationService, text: str = "@IA busca algo"):
    return await service.handle(
        raw_text=text,
        actor_id="users/alice",
        object_name="message",
        token="room1",
    )


@pytest.mark.asyncio
async def test_loop_executes_tool_then_returns_final_text():
    skill = FakeSkill(result=SkillResult.success({"dato": "ok"}))
    llm = DualLLM(
        tool_responses=[
            LLMToolResponse(
                tool_calls=(ToolCall(id="c1", name="fake_tool", arguments={"q": "hola"}),)
            ),
            LLMToolResponse(text="respuesta final"),
        ]
    )
    service = ConversationService(
        llm=llm, bot_mention_name=MENTION, skills=_registry(skill)
    )

    reply = await _handle(service)

    assert reply == "respuesta final"
    # La skill se ejecutó con los argumentos emitidos por el modelo.
    assert skill.calls[0][0] == {"q": "hola"}
    # Se llamó chat_with_tools dos veces; nunca se usó la ruta de texto puro.
    assert len(llm.tool_calls_log) == 2
    assert llm.complete_calls == []


@pytest.mark.asyncio
async def test_tool_result_is_appended_to_the_transcript():
    skill = FakeSkill(result=SkillResult.success({"dato": "ok"}))
    llm = DualLLM(
        tool_responses=[
            LLMToolResponse(
                tool_calls=(ToolCall(id="c1", name="fake_tool", arguments={"q": "hola"}),)
            ),
            LLMToolResponse(text="listo"),
        ]
    )
    service = ConversationService(
        llm=llm, bot_mention_name=MENTION, skills=_registry(skill)
    )

    await _handle(service)

    # La PRIMERA llamada ve L0 + fecha actual (slot L1/L2) + user; la SEGUNDA,
    # además, el turno de la tool-call del asistente y el del resultado.
    first, second = llm.tool_calls_log
    assert [type(m) for m in first] == [Message, Message, Message]
    assert isinstance(second[-2], AssistantToolCallTurn)
    assert isinstance(second[-1], ToolResultTurn)
    result_turn = second[-1]
    assert result_turn.tool_call_id == "c1"
    assert result_turn.name == "fake_tool"
    assert '"ok": true' in result_turn.content.lower()


@pytest.mark.asyncio
async def test_actor_context_resolves_impersonated_uid_from_actor_id():
    skill = FakeSkill()
    llm = DualLLM(
        tool_responses=[
            LLMToolResponse(tool_calls=(ToolCall(id="c1", name="fake_tool", arguments={}),)),
            LLMToolResponse(text="ok"),
        ]
    )
    service = ConversationService(
        llm=llm, bot_mention_name=MENTION, skills=_registry(skill)
    )

    await _handle(service)  # actor_id = "users/alice"

    _, actor = skill.calls[0]
    assert actor.actor_id == "users/alice"
    assert actor.token == "room1"
    assert actor.role_scope == "corporate"
    # ADR-016: `users/<uid>` se resuelve al uid impersonable.
    assert actor.impersonated_uid == "alice"


@pytest.mark.asyncio
async def test_agent_context_includes_current_date_from_code():
    # La fecha la pone el CÓDIGO (reloj inyectado), no el LLM: ancla anti-alucinación.
    skill = FakeSkill()
    llm = DualLLM(
        tool_responses=[
            LLMToolResponse(tool_calls=(ToolCall(id="c1", name="fake_tool", arguments={}),)),
            LLMToolResponse(text="ok"),
        ]
    )
    bogota = ZoneInfo("America/Bogota")
    service = ConversationService(
        llm=llm,
        bot_mention_name=MENTION,
        skills=_registry(skill),
        tz=bogota,
        now_fn=lambda: datetime(2026, 6, 30, 14, 30, tzinfo=bogota),
    )

    await _handle(service)

    first = llm.tool_calls_log[0]
    date_lines = [
        m.content
        for m in first
        if isinstance(m, Message) and m.role == "system"
        and m.content.startswith("Fecha y hora actuales:")
    ]
    assert len(date_lines) == 1
    assert "martes 30 de junio de 2026" in date_lines[0]
    assert "(America/Bogota)" in date_lines[0]


@pytest.mark.asyncio
async def test_without_registry_degrades_to_pure_text_completion():
    llm = DualLLM(text="hola desde complete")
    service = ConversationService(llm=llm, bot_mention_name=MENTION)  # skills=None

    reply = await _handle(service)

    assert reply == "hola desde complete"
    assert llm.complete_calls and not llm.tool_calls_log


@pytest.mark.asyncio
async def test_empty_registry_degrades_to_pure_text_completion():
    llm = DualLLM(text="texto")
    service = ConversationService(
        llm=llm, bot_mention_name=MENTION, skills=SkillRegistry()
    )

    await _handle(service)

    assert llm.complete_calls and not llm.tool_calls_log


@pytest.mark.asyncio
async def test_unknown_tool_name_feeds_error_back_and_loop_recovers():
    # El registry solo tiene 'fake_tool'; el modelo pide 'inexistente'.
    skill = FakeSkill(name="fake_tool")
    llm = DualLLM(
        tool_responses=[
            LLMToolResponse(
                tool_calls=(ToolCall(id="c1", name="inexistente", arguments={}),)
            ),
            LLMToolResponse(text="me recupero con texto"),
        ]
    )
    service = ConversationService(
        llm=llm, bot_mention_name=MENTION, skills=_registry(skill)
    )

    reply = await _handle(service)

    assert reply == "me recupero con texto"
    # El error de tool desconocida se anexó como resultado para que el modelo lo vea.
    result_turn = llm.tool_calls_log[1][-1]
    assert isinstance(result_turn, ToolResultTurn)
    assert "desconocida" in result_turn.content.lower()


@pytest.mark.asyncio
async def test_skill_exception_is_converted_to_failure_result():
    skill = FakeSkill(raises=RuntimeError("boom interno"))
    llm = DualLLM(
        tool_responses=[
            LLMToolResponse(tool_calls=(ToolCall(id="c1", name="fake_tool", arguments={}),)),
            LLMToolResponse(text="seguí adelante"),
        ]
    )
    service = ConversationService(
        llm=llm, bot_mention_name=MENTION, skills=_registry(skill)
    )

    reply = await _handle(service)

    assert reply == "seguí adelante"  # el fallo de skill no tumba el loop
    result_turn = llm.tool_calls_log[1][-1]
    assert '"ok": false' in result_turn.content.lower()


@pytest.mark.asyncio
async def test_iteration_cap_closes_with_fallback_and_is_bounded():
    skill = FakeSkill()
    # El modelo SIEMPRE pide tools, nunca cierra con texto: debe cortar el tope.
    always_tool = [
        LLMToolResponse(tool_calls=(ToolCall(id=f"c{i}", name="fake_tool", arguments={}),))
        for i in range(10)
    ]
    llm = DualLLM(tool_responses=always_tool)
    service = ConversationService(
        llm=llm,
        bot_mention_name=MENTION,
        skills=_registry(skill),
        agent_max_iterations=3,
    )

    reply = await _handle(service)

    assert reply == _AGENT_EXHAUSTED_MSG
    assert len(llm.tool_calls_log) == 3  # acotado al tope, no infinito


@pytest.mark.asyncio
async def test_final_reply_recorded_in_memory_but_not_tool_turns():
    skill = FakeSkill()
    memory = InMemoryConversationMemory(max_messages=10, ttl_seconds=3600)
    llm = DualLLM(
        tool_responses=[
            LLMToolResponse(tool_calls=(ToolCall(id="c1", name="fake_tool", arguments={}),)),
            LLMToolResponse(text="respuesta final del agente"),
        ]
    )
    service = ConversationService(
        llm=llm, bot_mention_name=MENTION, skills=_registry(skill), memory=memory
    )

    await _handle(service, text="@IA pregunta")

    roles = [(m.role, m.content) for m in memory.history("room1")]
    # Solo el turno humano y la respuesta final; los turnos de herramienta son
    # estado local del request (ADR-003), no memoria conversacional (ADR-014).
    assert roles == [
        ("user", "@IA pregunta"),
        ("assistant", "respuesta final del agente"),
    ]


@pytest.mark.asyncio
async def test_llm_error_in_loop_returns_fallback_and_skips_memory():
    class BoomToolLLM(DualLLM):
        async def chat_with_tools(self, messages, tools, *, model=None):
            raise LLMError("upstream caído")

    memory = InMemoryConversationMemory(max_messages=10, ttl_seconds=3600)
    service = ConversationService(
        llm=BoomToolLLM(),
        bot_mention_name=MENTION,
        skills=_registry(FakeSkill()),
        memory=memory,
    )

    reply = await _handle(service, text="@IA algo")

    assert reply == _FALLBACK_MSG
    # El humano se registró; el fallback de error NO contamina la memoria.
    assert [m.role for m in memory.history("room1")] == ["user"]


@pytest.mark.asyncio
async def test_non_zero_iteration_cap_is_validated():
    with pytest.raises(ValueError, match="agent_max_iterations"):
        ConversationService(
            llm=DualLLM(),
            bot_mention_name=MENTION,
            skills=_registry(FakeSkill()),
            agent_max_iterations=0,
        )

"""Port (interface) for LLM providers. Implementations live under app/adapters/."""
from __future__ import annotations

from typing import Protocol

from app.domain.message import Message
from app.domain.tool_calling import ConversationItem, LLMToolResponse, ToolSpec


class LLMPort(Protocol):
    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
    ) -> str:
        """Pure-text completion (Fase 1/2). Returns the assistant's reply text."""
        ...

    async def chat_with_tools(
        self,
        messages: list[ConversationItem],
        tools: list[ToolSpec],
        *,
        model: str | None = None,
    ) -> LLMToolResponse:
        """Tool-calling turn (ADR-017): offer ``tools`` and let the model route.

        Método NUEVO y ADITIVO: no sustituye a :meth:`complete` (la ruta de texto
        puro sigue intacta — OCP, ADR-002). El ``messages`` es el transcript del
        agente, que puede incluir turnos de herramienta
        (:class:`~app.domain.tool_calling.AssistantToolCallTurn` /
        :class:`~app.domain.tool_calling.ToolResultTurn`) además de
        :class:`~app.domain.message.Message`.

        Devuelve un :class:`~app.domain.tool_calling.LLMToolResponse` que modela
        los dos desenlaces: **(a)** el modelo pide *N* tool-calls, o **(b)**
        devuelve texto final. Los adapters que no soporten tools no implementan
        este método; su ruta :meth:`complete` no se ve afectada.
        """
        ...

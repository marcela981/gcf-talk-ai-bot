"""OpenAI implementation of the LLMPort.

Constructed eagerly but validates credentials lazily: instantiation with an
empty API key is allowed (so import-time wiring never crashes), and the
missing-key error is raised the first time `complete()` is invoked.

Implementa la ruta de texto puro (`complete`, Fase 1/2) y la ruta de
tool-calling (`chat_with_tools`, ADR-017) usando el function-calling nativo de
OpenAI. Esta es la ÚNICA capa que conoce el formato de wire del proveedor: traduce
los tipos de dominio (`Message`, los turnos de herramienta, `ToolSpec`) hacia/desde
el envelope de OpenAI, de modo que `services`/`domain` permanecen neutrales.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from openai import APIError, APITimeoutError, AsyncOpenAI

from app.domain.message import Message
from app.domain.tool_calling import (
    AssistantToolCallTurn,
    ConversationItem,
    LLMToolResponse,
    ToolCall,
    ToolResultTurn,
    ToolSpec,
)

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Adapter-level failure surfaced to the caller (network, auth, timeout)."""


class OpenAIAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        timeout_s: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._timeout_s = timeout_s
        self._client: AsyncOpenAI | None = None

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
    ) -> str:
        client = self._get_client()
        payload = [{"role": m.role, "content": m.content} for m in messages]
        try:
            response = await client.chat.completions.create(
                model=model or self._default_model,
                messages=payload,
            )
        except APITimeoutError as exc:
            raise LLMError(f"OpenAI request timed out: {exc}") from exc
        except APIError as exc:
            raise LLMError(f"OpenAI API error: {exc}") from exc
        return response.choices[0].message.content or ""

    async def chat_with_tools(
        self,
        messages: list[ConversationItem],
        tools: list[ToolSpec],
        *,
        model: str | None = None,
    ) -> LLMToolResponse:
        """Tool-calling turn via OpenAI's native function-calling (ADR-017).

        Traduce el transcript de dominio y los `ToolSpec` al wire de OpenAI,
        llama a `chat.completions.create` con `tools`, y mapea la respuesta a un
        `LLMToolResponse` (tool-calls solicitadas vs. texto final). Los errores de
        red/timeout se uniformizan como `LLMError`, igual que en `complete`.
        """
        client = self._get_client()
        payload = [_item_to_wire(item) for item in messages]
        wire_tools = [_tool_to_wire(t) for t in tools]
        try:
            response = await client.chat.completions.create(
                model=model or self._default_model,
                messages=payload,
                # OpenAI rechaza una lista `tools` vacía; con `None` se comporta
                # como una completion de texto (útil como cierre sin tools).
                tools=wire_tools or None,
            )
        except APITimeoutError as exc:
            raise LLMError(f"OpenAI request timed out: {exc}") from exc
        except APIError as exc:
            raise LLMError(f"OpenAI API error: {exc}") from exc

        choice = response.choices[0].message
        raw_calls = choice.tool_calls or []
        if raw_calls:
            parsed = tuple(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=_parse_arguments(tc.function.arguments),
                )
                for tc in raw_calls
            )
            return LLMToolResponse(text=choice.content, tool_calls=parsed)
        return LLMToolResponse(text=choice.content or "")

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            if not self._api_key:
                raise LLMError(
                    "OPENAI_API_KEY is not configured. Set it in the ExApp "
                    "environment before enabling LLM responses."
                )
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                timeout=self._timeout_s,
            )
        return self._client


# --- Wire translation (domain <-> OpenAI envelope) ---------------------------
# Aislado en funciones de módulo: es la frontera de traducción del proveedor.
# `services`/`domain` nunca ven estos dicts.


def _tool_to_wire(tool: ToolSpec) -> dict[str, Any]:
    """Map a neutral `ToolSpec` to OpenAI's `tools[]` function entry."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_schema,
        },
    }


def _item_to_wire(item: ConversationItem) -> dict[str, Any]:
    """Map one transcript item to an OpenAI `messages[]` entry."""
    if isinstance(item, Message):
        return {"role": item.role, "content": item.content}
    if isinstance(item, AssistantToolCallTurn):
        return {
            "role": "assistant",
            # Con tool_calls, OpenAI admite (y espera) content nulo.
            "content": None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in item.tool_calls
            ],
        }
    if isinstance(item, ToolResultTurn):
        return {
            "role": "tool",
            "tool_call_id": item.tool_call_id,
            "content": item.content,
        }
    raise TypeError(f"Unsupported conversation item: {type(item).__name__}")


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    """Parse the model's JSON argument string into a dict.

    Normalmente el proveedor garantiza JSON válido (validado contra el schema).
    Ante un raro JSON malformado o no-objeto, se degrada a ``{}`` y se loguea:
    la skill tratará los argumentos faltantes como fallo de datos y el modelo
    podrá recuperarse, en vez de tumbar el adapter.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Tool arguments were not valid JSON; treating as empty: %r", raw)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("Tool arguments were not a JSON object; treating as empty: %r", raw)
        return {}
    return parsed

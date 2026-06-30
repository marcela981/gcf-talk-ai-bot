"""Domain value types for the tool-calling agent engine (ADR-017). Pure data.

These cross every layer (services define the port, adapters do I/O, the loop in
the service uses them) so they live in ``domain`` and depend only on stdlib +
:class:`Message`. They model the two things the engine needs:

* What the LLM *advertises* it can call and what it *returns* when offered tools:
  :class:`ToolSpec` (a provider-neutral tool description) and
  :class:`LLMToolResponse` (either *N* requested tool-calls **or** final text).
* What the *agent transcript* looks like once tool-calls enter it. The plain
  text path (:class:`Message`) cannot carry a tool-call id or an assistant's
  ``tool_calls`` array, so two additive turn types model them:
  :class:`AssistantToolCallTurn` (the assistant asking for tools) and
  :class:`ToolResultTurn` (a tool's output fed back to the model). Keeping these
  *outside* :class:`Message` is deliberate — the pure-text route (``complete``)
  and its ``system/user/assistant`` invariant stay untouched (ADR-017, OCP).

``ConversationItem`` is the heterogeneous element type the agent loop appends to
and that :meth:`LLMPort.chat_with_tools` consumes; the adapter is the only place
that translates these to a provider's wire format.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from app.domain.message import Message


@dataclass(frozen=True)
class ToolSpec:
    """Provider-neutral description of a callable tool offered to the LLM.

    Mirrors a :class:`~app.services.skill.Skill`'s public surface (``name`` ==
    the tool name the model emits). It is intentionally *not* shaped like any
    vendor's schema: the adapter (e.g. OpenAI) maps it to the wire format, so
    ``services``/``domain`` never leak a provider's tool envelope.
    """

    name: str
    description: str
    parameters_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation requested by the model.

    * ``id``        — provider-issued correlation id; the matching
      :class:`ToolResultTurn` must echo it so the model pairs result to call.
    * ``name``      — the tool/skill name to resolve in the registry.
    * ``arguments`` — already-parsed JSON object emitted by the model (validated
      by the provider against the tool's ``parameters_schema``).
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AssistantToolCallTurn:
    """The assistant turn that requested one or more tool calls.

    Appended to the transcript verbatim before the tool results so the model
    sees its own request when the loop reiterates. Carries no text — when the
    model asks for tools, the content is the tool-calls themselves.
    """

    tool_calls: tuple[ToolCall, ...]


@dataclass(frozen=True)
class ToolResultTurn:
    """The output of executing one tool call, fed back to the model.

    ``content`` is the already-rendered string a
    :class:`~app.domain.skill_result.SkillResult` produces for the model to read.
    """

    tool_call_id: str
    name: str
    content: str


@dataclass(frozen=True)
class LLMToolResponse:
    """One turn's outcome from :meth:`LLMPort.chat_with_tools`.

    Models the two results ADR-017 requires: **(a)** the model asks for *N*
    tool-calls (``tool_calls`` non-empty), or **(b)** it returns final ``text``.
    Both fields may be populated (some providers attach narration to a tool
    request); the loop checks :attr:`is_tool_call` first, so tool-calls win.
    """

    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)

    @property
    def is_tool_call(self) -> bool:
        """True when the model requested at least one tool call."""
        return bool(self.tool_calls)


# The heterogeneous transcript the agent loop builds and chat_with_tools reads.
# Only the adapter translates these into a provider's message wire format.
ConversationItem = Union[Message, AssistantToolCallTurn, ToolResultTurn]

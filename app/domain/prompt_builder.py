"""Compose the message list sent to the LLM. Pure, no I/O."""
from __future__ import annotations

from app.domain.message import Message

DEFAULT_SYSTEM_PROMPT = (
    "Eres el asistente del portal empresarial. Responde de forma concisa "
    "y en español por defecto, salvo que el usuario escriba en otro idioma."
)


def build_messages(
    *,
    user_text: str,
    system_prompt: str | None = None,
) -> list[Message]:
    prompt = system_prompt if system_prompt is not None else DEFAULT_SYSTEM_PROMPT
    return [
        Message(role="system", content=prompt),
        Message(role="user", content=user_text),
    ]

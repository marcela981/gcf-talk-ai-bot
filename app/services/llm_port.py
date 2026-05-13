"""Port (interface) for LLM providers. Implementations live under app/adapters/."""
from __future__ import annotations

from typing import Protocol

from app.domain.message import Message


class LLMPort(Protocol):
    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
    ) -> str: ...

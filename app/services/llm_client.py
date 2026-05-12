"""Thin async wrapper around the OpenAI Chat Completions API.

Scaffolding for Week 2 — the Week 1 POC does not call this module. The bot
replies "pong" to "ping" deterministically. We keep the surface area defined
now so wiring it in next week is a small, low-risk change.
"""
from __future__ import annotations

from openai import AsyncOpenAI

from app.config import settings


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """Lazy singleton so importing the module never fails if the key is
    missing — only calling `complete()` does."""
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not configured. Set it in the ExApp "
                "environment before enabling LLM responses."
            )
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def complete(user_message: str, *, system_prompt: str = "") -> str:
    """Send `user_message` to ChatGPT and return the reply text."""
    client = _get_client()

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_message})

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
    )
    return response.choices[0].message.content or ""

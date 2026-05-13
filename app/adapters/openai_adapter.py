"""OpenAI implementation of the LLMPort.

Constructed eagerly but validates credentials lazily: instantiation with an
empty API key is allowed (so import-time wiring never crashes), and the
missing-key error is raised the first time `complete()` is invoked.
"""
from __future__ import annotations

from openai import APIError, APITimeoutError, AsyncOpenAI

from app.domain.message import Message


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

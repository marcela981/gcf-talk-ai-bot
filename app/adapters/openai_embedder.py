"""OpenAI implementation of EmbedderPort (ADR-008: text-embedding-3-small, 1536).

Misma política de credencial perezosa que `OpenAIAdapter`: instanciar con una
api_key vacía está permitido (el wiring de import nunca revienta); el error por
clave ausente se levanta en la primera llamada a `embed()`.
"""
from __future__ import annotations

from openai import APIError, APITimeoutError, AsyncOpenAI


class EmbedderError(Exception):
    """Fallo a nivel adapter de embeddings (red, auth, timeout)."""


class OpenAIEmbedder:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "text-embedding-3-small",
        timeout_s: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._client: AsyncOpenAI | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        try:
            response = await client.embeddings.create(model=self._model, input=texts)
        except APITimeoutError as exc:
            raise EmbedderError(f"OpenAI embeddings timed out: {exc}") from exc
        except APIError as exc:
            raise EmbedderError(f"OpenAI embeddings API error: {exc}") from exc
        # La API devuelve los embeddings en orden; `index` lo confirma.
        ordered = sorted(response.data, key=lambda d: d.index)
        return [item.embedding for item in ordered]

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            if not self._api_key:
                raise EmbedderError(
                    "OPENAI_API_KEY is not configured. Set it before running "
                    "embeddings (query retrieval or ingestion)."
                )
            self._client = AsyncOpenAI(api_key=self._api_key, timeout=self._timeout_s)
        return self._client

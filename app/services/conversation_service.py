"""Use case: turn an inbound Talk event into an optional reply string.

Owns the trigger decision, prompt assembly, LLM invocation, and the
user-facing fallback when the adapter fails.

Fase 2 (RAG): cuando el servicio se construye con un `EmbedderPort`, un
`RetrievalPort` y una `RetrievalPolicy`, antes de llamar al LLM se recupera
contexto corporativo por similitud y se inyecta en el slot L2 vía el parámetro
aditivo `extra_system` de `build_messages` (L0 permanece inmutable).

La recuperación es ADITIVA y best-effort: si falla (red, embedder, store), se
degrada con elegancia respondiendo sin contexto en lugar de tumbar la respuesta.
Las dependencias RAG son opcionales: sin ellas, el comportamiento es idéntico al
de la Fase 1.
"""
from __future__ import annotations

import logging

from app.adapters.openai_adapter import LLMError
from app.domain.context_assembly import assemble_context_block
from app.domain.message_policy import should_reply, strip_invocation
from app.domain.prompt_builder import build_messages
from app.domain.retrieval_policy import RetrievalPolicy
from app.services.embedder_port import EmbedderPort
from app.services.llm_port import LLMPort
from app.services.retrieval_port import RetrievalPort

logger = logging.getLogger(__name__)

_FALLBACK_MSG = (
    "Lo siento, no pude procesar tu mensaje en este momento. "
    "Inténtalo de nuevo en unos segundos."
)


class ConversationService:
    def __init__(
        self,
        llm: LLMPort,
        bot_mention_name: str,
        *,
        embedder: EmbedderPort | None = None,
        retrieval: RetrievalPort | None = None,
        retrieval_policy: RetrievalPolicy | None = None,
        role_scope: str = "corporate",
    ) -> None:
        self._llm = llm
        self._bot_mention_name = bot_mention_name
        self._embedder = embedder
        self._retrieval = retrieval
        self._retrieval_policy = retrieval_policy
        self._role_scope = role_scope

    @property
    def _rag_ready(self) -> bool:
        return (
            self._embedder is not None
            and self._retrieval is not None
            and self._retrieval_policy is not None
        )

    async def handle(
        self,
        *,
        raw_text: str,
        actor_id: str,
        object_name: str,
    ) -> str | None:
        if not should_reply(
            raw_text=raw_text,
            actor_id=actor_id,
            object_name=object_name,
            bot_mention_name=self._bot_mention_name,
        ):
            return None

        clean = strip_invocation(raw_text, self._bot_mention_name)
        extra_system = await self._retrieve_context(clean)
        messages = build_messages(user_text=clean, extra_system=extra_system)
        try:
            return await self._llm.complete(messages)
        except LLMError:
            logger.exception("LLM completion failed; returning fallback reply.")
            return _FALLBACK_MSG

    async def _retrieve_context(self, query: str) -> list[str] | None:
        """Recupera contexto y lo devuelve como bloque(s) para `extra_system`.

        Devuelve ``None`` cuando no hay RAG cableado, no procede recuperar, no
        hay fragmentos pertinentes, o la recuperación falla (degradación). El
        fallo de recuperación NO debe propagarse: el contexto es aditivo.
        """
        if not self._rag_ready:
            return None
        assert self._embedder is not None  # noqa: S101 — narrowing for type checker
        assert self._retrieval is not None
        assert self._retrieval_policy is not None

        if not self._retrieval_policy.should_retrieve(query):
            return None

        try:
            (query_embedding,) = await self._embedder.embed([query])
            chunks = await self._retrieval.search(query_embedding, self._role_scope)
        except Exception:  # best-effort: cualquier fallo => responder sin contexto
            logger.exception("Recuperación RAG falló; se responde sin contexto.")
            return None

        selected = self._retrieval_policy.select(chunks)
        block = assemble_context_block(selected)
        return [block] if block is not None else None

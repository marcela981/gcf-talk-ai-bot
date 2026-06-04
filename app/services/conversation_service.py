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

Memoria conversacional (ADR-014, Opción B): cuando el servicio se construye con
un `ConversationMemoryPort`, registra cada turno humano de la sala y reproduce el
buffer previo como `history` al construir el prompt. Es OPCIONAL e igualmente
best-effort respecto al comportamiento base: con `memory=None` el flujo es
idéntico al de la Fase 1 (sin historia). El registro ocurre para TODO mensaje
humano (no solo menciones), de modo que las salas acumulen contexto aunque el bot
calle; el eco de las propias respuestas del bot (actor `bots/`) NO se registra en
el camino de entrada — el turno `assistant` se registra explícitamente al salir.
"""
from __future__ import annotations

import logging

from app.adapters.openai_adapter import LLMError
from app.domain.context_assembly import assemble_context_block
from app.domain.message import Message
from app.domain.message_policy import should_reply, strip_invocation
from app.domain.prompt_builder import build_messages
from app.domain.retrieval_policy import RetrievalPolicy
from app.services.conversation_memory_port import ConversationMemoryPort
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
        memory: ConversationMemoryPort | None = None,
        role_scope: str = "corporate",
    ) -> None:
        self._llm = llm
        self._bot_mention_name = bot_mention_name
        self._embedder = embedder
        self._retrieval = retrieval
        self._retrieval_policy = retrieval_policy
        self._memory = memory
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
        token: str,
    ) -> str | None:
        # Tomar la historia ANTES de registrar el mensaje actual evita que el
        # propio mensaje aparezca duplicado en su contexto. El registro del
        # turno humano ocurre aunque el bot no responda: las salas acumulan
        # contexto incluso cuando calla.
        snapshot = self._recall_and_record_inbound(
            token=token,
            raw_text=raw_text,
            actor_id=actor_id,
            object_name=object_name,
        )

        if not should_reply(
            raw_text=raw_text,
            actor_id=actor_id,
            object_name=object_name,
            bot_mention_name=self._bot_mention_name,
        ):
            return None

        clean = strip_invocation(raw_text, self._bot_mention_name)
        extra_system = await self._retrieve_context(clean)
        messages = build_messages(
            user_text=clean,
            history=snapshot,
            extra_system=extra_system,
        )
        try:
            reply = await self._llm.complete(messages)
        except LLMError:
            logger.exception("LLM completion failed; returning fallback reply.")
            # El fallback NO se registra como turno `assistant`: es un mensaje de
            # error de transporte, no contenido conversacional. Registrarlo
            # contaminaría el contexto de los siguientes turnos.
            return _FALLBACK_MSG

        self._record_assistant(token, reply)
        return reply

    def _recall_and_record_inbound(
        self,
        *,
        token: str,
        raw_text: str,
        actor_id: str,
        object_name: str,
    ) -> list[Message]:
        """Devuelve la historia previa y registra el turno humano entrante.

        Con `memory=None` devuelve ``[]`` (comportamiento Fase 1: sin historia).
        Solo registra mensajes humanos reales: descarta el eco de otros bots
        (`actor_id` con prefijo ``bots/``, incluido el propio), eventos que no son
        de chat (`object_name != "message"`: reacciones, joins…) y texto vacío.
        """
        if self._memory is None:
            return []
        snapshot = self._memory.history(token)
        if (
            object_name == "message"
            and not actor_id.startswith("bots/")
            and raw_text.strip()
        ):
            self._memory.record(token, "user", actor_id, raw_text)
        return snapshot

    def _record_assistant(self, token: str, reply: str) -> None:
        if self._memory is not None:
            self._memory.record(token, "assistant", self._bot_mention_name, reply)

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

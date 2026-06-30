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

Motor de agente (ADR-017/ADR-018): cuando el servicio se construye con un
`SkillRegistry` NO vacío, la ruta de respuesta es un *tool-use loop* en vez de un
único `complete`. Por iteración: `chat_with_tools` ofrece las skills como tools;
si el modelo pide tool-calls, cada una se ejecuta vía el registry y su resultado
se anexa como turno de herramienta al transcript LOCAL del request (stateless,
ADR-003), y se reitera; si el modelo devuelve texto final, ese es el `reply`. Un
tope de iteraciones acota coste/latencia. DEGRADACIÓN: sin registry (o vacío), el
servicio se comporta como Fase 1/2 (responde con `complete`, con/ sin contexto L2)
— la ruta de texto puro NO se rompe. En modo agente el contexto corporativo se
recupera *como tool* (`consultar_base_conocimiento`), no por inyección automática
de L2: el router es el LLM (ADR-017), que decide cuándo buscar.
"""
from __future__ import annotations

import logging

from app.adapters.openai_adapter import LLMError
from app.domain.actor_context import ActorContext
from app.domain.context_assembly import assemble_context_block
from app.domain.identity import resolve_impersonated_uid
from app.domain.message import Message
from app.domain.message_policy import should_reply, strip_invocation
from app.domain.prompt_builder import build_messages
from app.domain.retrieval_policy import RetrievalPolicy
from app.domain.tool_calling import (
    AssistantToolCallTurn,
    ConversationItem,
    ToolCall,
    ToolResultTurn,
)
from app.services.conversation_memory_port import ConversationMemoryPort
from app.services.embedder_port import EmbedderPort
from app.services.llm_port import LLMPort
from app.services.retrieval_port import RetrievalPort
from app.services.skill_registry import SkillRegistry
from app.services.skill import Skill
from app.domain.skill_result import SkillResult

logger = logging.getLogger(__name__)

_FALLBACK_MSG = (
    "Lo siento, no pude procesar tu mensaje en este momento. "
    "Inténtalo de nuevo en unos segundos."
)

# Texto de cierre si el loop agota el tope de iteraciones sin un texto final.
_AGENT_EXHAUSTED_MSG = (
    "No logré completar la consulta tras varios intentos. "
    "¿Podrías reformularla o darme más detalles?"
)

_DEFAULT_MAX_AGENT_ITERATIONS = 5


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
        skills: SkillRegistry | None = None,
        agent_max_iterations: int = _DEFAULT_MAX_AGENT_ITERATIONS,
        role_scope: str = "corporate",
    ) -> None:
        self._llm = llm
        self._bot_mention_name = bot_mention_name
        self._embedder = embedder
        self._retrieval = retrieval
        self._retrieval_policy = retrieval_policy
        self._memory = memory
        self._skills = skills
        if agent_max_iterations <= 0:
            raise ValueError(
                f"agent_max_iterations debe ser > 0; recibido {agent_max_iterations!r}."
            )
        self._agent_max_iterations = agent_max_iterations
        self._role_scope = role_scope

    @property
    def _rag_ready(self) -> bool:
        return (
            self._embedder is not None
            and self._retrieval is not None
            and self._retrieval_policy is not None
        )

    @property
    def _agent_ready(self) -> bool:
        """True cuando hay un registry con al menos una skill cableada.

        Es el interruptor de modo: con él activo la respuesta sale del tool-use
        loop (ADR-017); sin él, de la ruta de texto puro `complete` (Fase 1/2).
        """
        return self._skills is not None and len(self._skills) > 0

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
        try:
            if self._agent_ready:
                actor = ActorContext(
                    actor_id=actor_id,
                    token=token,
                    role_scope=self._role_scope,
                    # ADR-016: identidad resuelta en la ruta del webhook. `users/<uid>`
                    # → uid impersonable; invitados/federados → None (la skill con
                    # efectos se rehúsa). Los `bots/` ya se filtraron (anti-loop).
                    impersonated_uid=resolve_impersonated_uid(actor_id),
                )
                reply = await self._run_agent_loop(clean, snapshot, actor)
            else:
                reply = await self._complete_text(clean, snapshot)
        except LLMError:
            logger.exception("LLM completion failed; returning fallback reply.")
            # El fallback NO se registra como turno `assistant`: es un mensaje de
            # error de transporte, no contenido conversacional. Registrarlo
            # contaminaría el contexto de los siguientes turnos.
            return _FALLBACK_MSG

        self._record_assistant(token, reply)
        return reply

    async def _complete_text(self, clean: str, snapshot: list[Message]) -> str:
        """Ruta de texto puro (Fase 1/2): RAG aditivo en L2 + un solo `complete`."""
        extra_system = await self._retrieve_context(clean)
        messages = build_messages(
            user_text=clean,
            history=snapshot,
            extra_system=extra_system,
        )
        return await self._llm.complete(messages)

    async def _run_agent_loop(
        self,
        clean: str,
        snapshot: list[Message],
        actor: ActorContext,
    ) -> str:
        """Tool-use loop (ADR-017). Todo el estado vive en variables locales.

        Semilla del transcript = L0 + historia + mensaje del usuario (sin L2
        automático: el contexto se recupera *como tool*). Por iteración pide
        `chat_with_tools`; si hay tool-calls, las ejecuta y anexa sus resultados;
        si hay texto final, lo devuelve. Al agotar el tope cierra con el mejor
        texto visto o un mensaje de cierre (acota coste/latencia — ADR-017).
        """
        assert self._skills is not None  # narrowing; garantizado por _agent_ready
        tools = self._skills.tool_specs()
        conversation: list[ConversationItem] = list(
            build_messages(user_text=clean, history=snapshot)
        )

        best_text: str | None = None
        for _ in range(self._agent_max_iterations):
            response = await self._llm.chat_with_tools(conversation, tools)
            if response.text:
                best_text = response.text
            if not response.is_tool_call:
                # Texto final: fin del loop.
                return response.text or _AGENT_EXHAUSTED_MSG

            # El modelo pidió tools: anexa su turno y los resultados, y reitera.
            conversation.append(AssistantToolCallTurn(tool_calls=response.tool_calls))
            for call in response.tool_calls:
                result = await self._execute_tool(call, actor)
                conversation.append(
                    ToolResultTurn(
                        tool_call_id=call.id,
                        name=call.name,
                        content=result.to_tool_content(),
                    )
                )

        logger.warning(
            "Tool-use loop agotó el tope de %d iteraciones; cierre con mejor texto.",
            self._agent_max_iterations,
        )
        return best_text or _AGENT_EXHAUSTED_MSG

    async def _execute_tool(self, call: ToolCall, actor: ActorContext) -> SkillResult:
        """Resuelve la skill por nombre y la ejecuta; los fallos vuelven como dato.

        Una tool desconocida (el modelo no debería pedirla — solo se anuncian las
        registradas) o una excepción en `execute` se traducen a `SkillResult.failure`
        para que el modelo se recupere; nunca tumban el loop.
        """
        assert self._skills is not None  # narrowing
        try:
            skill: Skill = self._skills.get(call.name)
        except KeyError:
            logger.warning("El modelo pidió una tool no registrada: %r.", call.name)
            return SkillResult.failure(f"Tool desconocida: {call.name!r}.")
        try:
            return await skill.execute(call.arguments, actor)
        except Exception as exc:  # noqa: BLE001 — resiliencia del loop
            logger.exception("La skill %r falló durante su ejecución.", call.name)
            return SkillResult.failure(f"Error ejecutando la skill: {exc}")

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

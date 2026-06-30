"""Entry point for the GCF Talk AI Bot ExApp.

Wires together:
  * FastAPI app with AppAPI's authentication middleware (verifies the shared
    secret on every request coming from Nextcloud).
  * AppAPI lifecycle endpoints (/init, /enabled, /heartbeat) registered via
    `set_handlers`.
  * A Talk bot webhook with automatic HMAC-SHA256 signature verification
    through the `atalk_bot_msg` dependency.

ASYNC MIGRATION (nc_py_api 0.30.x):
  nc_py_api >= 0.30 deprecated the synchronous `TalkBot` / sync lifecycle
  handlers. Under a FastAPI async `lifespan`, a *synchronous* enabled_handler
  is not awaited correctly by `set_handlers`. Hence the full async path:
    * talk_bot.TalkBot          -> talk_bot.AsyncTalkBot
    * def enabled_handler        -> async def enabled_handler
    * NextcloudApp               -> AsyncNextcloudApp

TALK BOT REGISTRATION API:
  The bot is registered with Talk through the *NextcloudApp* object, NOT the
  AsyncTalkBot object. AsyncTalkBot only carries identity (callback_url,
  display_name, description) and is used to *receive/answer* messages. The
  registration verbs live on `nc`:
    * nc.register_talk_bot(callback_url, display_name, description)
        -> registers the bot, AppAPI writes a row in appconfig_ex
    * nc.unregister_talk_bot(callback_url)
        -> removes it
  An earlier revision called BOT.enable_bot(nc), which never existed on
  AsyncTalkBot (AttributeError at enable time): the ExApp showed [enabled]
  in AppAPI but never appeared in `talk:bot:list`.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import Response

from nc_py_api import AsyncNextcloudApp, talk_bot
from nc_py_api.ex_app import (
    AppAPIAuthMiddleware,
    atalk_bot_msg,
    run_app,
    set_handlers,
)

from app.adapters.openai_adapter import OpenAIAdapter
from app.config import settings
from app.domain.retrieval_policy import RetrievalPolicy
from app.handlers.talk_handler import handle_message
from app.services.conversation_service import ConversationService


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


_BOT_CALLBACK_URL = "/talk_bot"

# AsyncTalkBot carries the bot's identity and is the object used to *answer*
# messages (atalk_bot_msg yields TalkBotMessage; .send_message replies).
# It does NOT register itself — that is done via AsyncNextcloudApp below.
BOT = talk_bot.AsyncTalkBot(
    callback_url=_BOT_CALLBACK_URL,
    display_name=settings.bot_display_name,
    description=settings.bot_description,
)

# Built at import time. The adapter tolerates an empty api_key here and only
# raises when `complete()` is actually invoked, so import never fails because
# OPENAI_API_KEY is unset in some environments (e.g. CI, local checks).
_adapter = OpenAIAdapter(
    api_key=settings.openai_api_key,
    default_model=settings.openai_model,
)

# --- Fase 2 (RAG): cableado del slot L2 -------------------------------------
# La recuperación es aditiva y best-effort. Solo se cablea si hay config mínima
# (PGVECTOR_DSN + OPENAI_API_KEY); en otro caso el servicio degrada al
# comportamiento de la Fase 1 (responde sin contexto). Los adapters de RAG
# (psycopg/pgvector, openai embedder) se importan de forma perezosa para que el
# import de main.py no exija esas dependencias cuando RAG está deshabilitado.
_embedder = None
_retrieval = None
_retrieval_policy = None
if settings.rag_enabled:
    from app.adapters.openai_embedder import OpenAIEmbedder
    from app.adapters.pgvector_store import PgVectorStore

    _retrieval_policy = RetrievalPolicy(
        top_k=settings.rag_top_k,
        similarity_threshold=settings.rag_similarity_threshold,
    )
    _embedder = OpenAIEmbedder(
        api_key=settings.openai_api_key,
        model=settings.embedding_model,
    )
    _retrieval = PgVectorStore(
        dsn=settings.pgvector_dsn,
        top_k=settings.rag_top_k,
        similarity_threshold=settings.rag_similarity_threshold,
    )
    logger.info("RAG habilitado: contexto corporativo se inyectará en L2.")
else:
    logger.info("RAG deshabilitado (sin PGVECTOR_DSN/OPENAI_API_KEY); modo Fase 1.")

# --- ADR-014: memoria conversacional por sala (buffer in-memory) ------------
# Singleton del proceso: un único buffer compartido por todas las salas (aislado
# por token internamente). Solo se cablea si está habilitada; con None el
# servicio degrada al comportamiento de la Fase 1 (sin historia). CONSTRAINT:
# el buffer NO se comparte entre workers — el despliegue debe correr con 1 worker
# (deuda D7). El adapter es stdlib-only, así que su import nunca falla.
_memory = None
if settings.conversation_memory_enabled:
    from app.adapters.in_memory_conversation_memory import InMemoryConversationMemory

    _memory = InMemoryConversationMemory(
        max_messages=settings.conversation_history_max_messages,
        ttl_seconds=settings.conversation_history_ttl_seconds,
    )
    logger.info(
        "Memoria conversacional habilitada: buffer in-memory por sala "
        "(max=%d turnos, ttl=%ds). Requiere 1 worker (deuda D7).",
        settings.conversation_history_max_messages,
        settings.conversation_history_ttl_seconds,
    )
else:
    logger.info("Memoria conversacional deshabilitada; modo Fase 1 (sin historia).")

# --- ADR-016/017/018: motor de agente (tool-calling) + SkillRegistry --------
# Composition root del catálogo de skills. AGENT_ENABLED es el ÚNICO interruptor
# maestro del modo agente (default false). El motor es agnóstico de capacidades
# (ADR-017): el registry se puebla por la dependencia PROPIA de cada skill, NO por
# un gate global:
#   * KnowledgeBaseSkill -> sii rag_enabled (reusa embedder + vector store).
#   * ResumenAgendaSkill -> sii appapi_ready (cliente CalDAV firmado propio; ADR-016).
# El agente queda ACTIVO sii agent_enabled y el registry no está vacío; si no,
# `skills=None` ⇒ el servicio degrada a la ruta Fase 1/2 (`complete`, con contexto
# L2 automático si hay RAG). Alta de skill = nueva clase + `register(...)` AQUÍ; el
# motor (ConversationService) y el LLMPort NO se tocan (OCP).
_skills = None
if settings.agent_enabled:
    from app.services.skill_registry import SkillRegistry

    _registry = SkillRegistry()

    if settings.rag_enabled:
        from app.adapters.knowledge_base_skill import KnowledgeBaseSkill

        _registry.register(
            KnowledgeBaseSkill(
                embedder=_embedder,
                retrieval=_retrieval,
                retrieval_policy=_retrieval_policy,
                role_scope=settings.rag_default_role_scope,
            )
        )

    if settings.appapi_ready:
        from app.adapters.calendar_skill import ResumenAgendaSkill
        from app.adapters.nextcloud_calendar_adapter import NextcloudCalendarAdapter

        _calendar = NextcloudCalendarAdapter(
            endpoint=settings.nextcloud_url,
            app_id=settings.app_id,
            app_version=settings.app_version,
            app_secret=settings.app_secret,
            aa_version=settings.aa_version,
            dav_url_suffix=settings.dav_url_suffix,
        )
        _registry.register(ResumenAgendaSkill(calendar=_calendar))

    if len(_registry) > 0:
        _skills = _registry
        logger.info(
            "Motor de agente ACTIVO: %d skill(s) registrada(s); el LLM enruta por "
            "tool-calling (tope %d iteraciones).",
            len(_registry),
            settings.agent_max_iterations,
        )
    else:
        logger.info(
            "AGENT_ENABLED=true pero sin skills cableables (rag_enabled=%s, "
            "appapi_ready=%s); se degrada a la ruta de texto Fase 1/2.",
            settings.rag_enabled,
            settings.appapi_ready,
        )
else:
    logger.info("Modo agente desactivado (AGENT_ENABLED=false); ruta Fase 1/2.")

_service = ConversationService(
    llm=_adapter,
    bot_mention_name=settings.bot_mention_name,
    embedder=_embedder,
    retrieval=_retrieval,
    retrieval_policy=_retrieval_policy,
    memory=_memory,
    skills=_skills,
    agent_max_iterations=settings.agent_max_iterations,
    role_scope=settings.rag_default_role_scope,
)


async def enabled_handler(enabled: bool, nc: AsyncNextcloudApp) -> str:
    """Invoked by AppAPI when the operator enables or disables the ExApp.

    Registers (or unregisters) the bot with the Talk app so Nextcloud knows
    where to deliver chat webhooks. AppAPI expects an empty string on
    success or an error message on failure.

    Registration goes through `nc` (AsyncNextcloudApp), not through BOT:
      * register_talk_bot writes the callback + signing secret into Talk;
        after this the bot shows up in `occ talk:bot:list`.
      * unregister_talk_bot removes it on disable.
    """
    try:
        if enabled:
            await nc.register_talk_bot(
                _BOT_CALLBACK_URL,
                settings.bot_display_name,
                settings.bot_description,
            )
            logger.info("Bot registered with Talk.")
        else:
            await nc.unregister_talk_bot(_BOT_CALLBACK_URL)
            logger.info("Bot unregistered from Talk.")
    except Exception as exc:
        logger.exception("enabled_handler failed")
        return str(exc)
    return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    # set_handlers wires the AppAPI lifecycle routes onto `app`:
    #   POST /init       — initialization hook
    #   PUT  /enabled    — calls enabled_handler(enabled: bool, nc)
    #   GET  /heartbeat  — liveness probe (used by AppAPI and Docker)
    set_handlers(app, enabled_handler)
    yield


APP = FastAPI(lifespan=lifespan)
# Validates every inbound request against the ExApp shared secret + headers
# AppAPI sets (EX-APP-ID, AUTHORIZATION-APP-API, ...). Requests that don't
# come from a trusted Nextcloud instance are rejected with HTTP 401.
#
# SPIKE — REMOVE BEFORE MERGE: `disable_for=["debug/files-spike"]` exposes the
# spike endpoint without AppAPI signing. The container only listens on the
# internal Docker network (no ports: in docker-compose.yml), so the surface
# is limited to operators with shell access to that network.
APP.add_middleware(AppAPIAuthMiddleware, disable_for=["debug/files-spike"])


@APP.post(_BOT_CALLBACK_URL)
async def talk_bot_webhook(
     message: Annotated[talk_bot.TalkBotMessage, Depends(atalk_bot_msg)],
) -> Response:
    """Talk delivers chat events here.

    `atalk_bot_msg` is the security boundary for this route:
      1. Reads X-Nextcloud-Talk-Random and X-Nextcloud-Talk-Signature.
      2. Recomputes HMAC-SHA256(secret, random + body) and constant-time
         compares it against the provided signature.
      3. Rejects with HTTP 401 if the signature is missing or invalid.

    By the time the body of this function runs, the message is authenticated.
    """
    await handle_message(message, _service, BOT)
    return Response(status_code=200)


# SPIKE — REMOVE BEFORE MERGE -------------------------------------------------
# Temporary debug endpoint to drive the Nextcloud Files spike (ADR-006). Bound
# to POST so it never gets hit by a casual browser probe; payload is ignored.
# The endpoint is excluded from AppAPIAuthMiddleware (see disable_for above).
# Gated by an env var so the route is not even registered in production.
if os.environ.get("SPIKE_FILES_ENABLED") == "1":
    from app._spike.nextcloud_files_spike import run_spike  # SPIKE import

    @APP.post("/debug/files-spike")  # SPIKE — REMOVE BEFORE MERGE
    async def _debug_files_spike() -> dict:
        return await run_spike()
# SPIKE END -------------------------------------------------------------------


if __name__ == "__main__":
    # `run_app` reads APP_HOST / APP_PORT from the environment, which AppAPI
    # injects at deploy time (or .env in local development).
    run_app("app.main:APP", log_level="info")
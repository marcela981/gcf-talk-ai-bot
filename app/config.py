"""Application configuration.

Reads runtime settings from environment variables. AppAPI injects most of the
infrastructure variables (APP_ID, APP_SECRET, APP_PORT, NEXTCLOUD_URL, ...)
at deployment time; nc_py_api consumes those directly. The operator only has
to set the OpenAI credentials and the bot's display strings.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Best-effort .env loading for local development. In the container, AppAPI
# is the source of truth and python-dotenv is a no-op.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


@dataclass(frozen=True)
class Settings:
    bot_display_name: str
    bot_mention_name: str
    bot_description: str
    openai_api_key: str
    openai_model: str
    # --- Fase 2: RAG ---------------------------------------------------------
    # Empty defaults are intentional: import must never fail because a RAG
    # secret is unset (mirrors the lazy-validation policy of OpenAIAdapter).
    # The adapters validate their own credentials on first use; conversation
    # falls back to the Fase 1 behaviour (no retrieved context) when RAG is
    # not wired (see `rag_enabled`).
    supabase_url: str
    supabase_key: str
    # DEPRECADO (ADR-006-ter): el corpus ya no vive en un bucket sino en la
    # tabla-catálogo `documents`. Se conserva para no romper despliegues que aún
    # lo definan; ningún código vigente lo consume.
    supabase_bucket: str
    pgvector_dsn: str
    embedding_model: str
    rag_top_k: int
    rag_similarity_threshold: float
    rag_default_role_scope: str
    # Niveles de access_level a ingerir desde la tabla-catálogo (ADR-006-ter).
    rag_ingest_levels: tuple[str, ...]
    # --- ADR-014: memoria conversacional por sala (buffer in-memory) ---------
    # Efímera y opcional. Con `conversation_memory_enabled=False` el bot degrada
    # al comportamiento de la Fase 1 (sin historia). Los límites acotan el buffer
    # en RAM (cota de turnos por sala + expiración por entrada).
    conversation_memory_enabled: bool
    conversation_history_max_messages: int
    conversation_history_ttl_seconds: int
    # --- Motor de agente / tool-calling (ADR-017/ADR-018) --------------------
    # `agent_enabled` es el ÚNICO interruptor maestro del modo agente (default
    # False; opt-in explícito en el deployment). NO está acoplado a RAG: el motor
    # es agnóstico de capacidades (ADR-017) y cada skill se cablea según SU propia
    # dependencia (RAG para la base de conocimiento, AppAPI para el calendario).
    # Con el agente activo la respuesta sale del tool-use loop; si no, degrada a la
    # ruta Fase 1/2 (`complete`, con contexto L2 automático si hay RAG). El tope de
    # iteraciones acota coste/latencia del loop.
    agent_enabled: bool
    agent_max_iterations: int
    # --- AppAPI / impersonation (ADR-016): identidad para skills de Nextcloud --
    # AppAPI inyecta estas variables en el contenedor; nc_py_api las consume directo,
    # pero las skills que construyen su PROPIO cliente firmado (p. ej. el calendario
    # CalDAV) también las necesitan. Defaults vacíos: el import nunca falla por una
    # var ausente; el adapter valida sus credenciales en su primer uso.
    nextcloud_url: str
    app_id: str
    app_version: str
    app_secret: str
    aa_version: str
    dav_url_suffix: str

    @property
    def appapi_ready(self) -> bool:
        """True cuando hay config mínima para hablar con Nextcloud impersonando.

        La skill de calendario (CalDAV) construye su propio cliente firmado y
        necesita la URL de Nextcloud + las credenciales del ExApp (APP_ID/APP_SECRET)
        que AppAPI inyecta. Sin ellas, esa skill no se registra (degradación).
        """
        return bool(self.nextcloud_url and self.app_id and self.app_secret)

    @property
    def rag_enabled(self) -> bool:
        """True when the retrieval path has the minimum config to run.

        The bot only needs the vector store (read) + an OpenAI key (to embed
        the query) to answer with context. Supabase is only required by the
        ingestion CLI, not by the request path.
        """
        return bool(self.pgvector_dsn and self.openai_api_key)


def _load() -> Settings:
    return Settings(
        bot_display_name=os.environ.get("BOT_DISPLAY_NAME", "GCF AI Bot"),
        bot_mention_name=os.environ.get("BOT_MENTION_NAME", "IA"),
        bot_description=os.environ.get(
            "BOT_DESCRIPTION",
            "AI-powered assistant using OpenAI ChatGPT.",
        ),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        supabase_url=os.environ.get("SUPABASE_URL", ""),
        supabase_key=os.environ.get("SUPABASE_KEY", ""),
        supabase_bucket=os.environ.get("SUPABASE_BUCKET", ""),
        pgvector_dsn=os.environ.get("PGVECTOR_DSN", ""),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
        rag_top_k=int(os.environ.get("RAG_TOP_K", "4")),
        rag_similarity_threshold=float(
            os.environ.get("RAG_SIMILARITY_THRESHOLD", "0.75")
        ),
        rag_default_role_scope=os.environ.get("RAG_DEFAULT_ROLE_SCOPE", "corporate"),
        rag_ingest_levels=tuple(
            part.strip()
            for part in os.environ.get("RAG_INGEST_LEVELS", "noroot").split(",")
            if part.strip()
        ),
        conversation_memory_enabled=_env_bool("CONVERSATION_MEMORY_ENABLED", True),
        conversation_history_max_messages=int(
            os.environ.get("CONVERSATION_HISTORY_MAX_MESSAGES", "10")
        ),
        conversation_history_ttl_seconds=int(
            os.environ.get("CONVERSATION_HISTORY_TTL_SECONDS", "3600")
        ),
        agent_enabled=_env_bool("AGENT_ENABLED", False),
        agent_max_iterations=int(os.environ.get("AGENT_MAX_ITERATIONS", "5")),
        nextcloud_url=os.environ.get("NEXTCLOUD_URL", ""),
        app_id=os.environ.get("APP_ID", ""),
        app_version=os.environ.get("APP_VERSION", ""),
        app_secret=os.environ.get("APP_SECRET", ""),
        aa_version=os.environ.get("AA_VERSION", "2.2.0"),
        dav_url_suffix=os.environ.get("DAV_URL_SUFFIX", "remote.php/dav"),
    )


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var. Unset → `default`; never raises on import."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


settings = _load()

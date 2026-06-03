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
    )


settings = _load()

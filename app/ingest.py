"""Entrypoint CLI de ingestión del corpus RAG (ADR-010: on-demand, NO endpoint).

Uso:
    python -m app.ingest

Recorre Supabase Storage (read-only), chunkea (tiktoken cl100k_base, 500 tokens),
embebe (text-embedding-3-small) y hace upsert idempotente en pgvector.

Es el composition root de la ingestión: aquí —y solo aquí— se instancian los
adapters concretos y se inyectan en `IngestionService`. Mantiene el patrón
hexagonal: el servicio no conoce httpx, openai ni psycopg.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from app.adapters.openai_embedder import OpenAIEmbedder
from app.adapters.pgvector_store import PgVectorStore
from app.adapters.supabase_storage_loader import SupabaseStorageLoader
from app.adapters.tiktoken_encoder import TiktokenEncoder
from app.config import settings
from app.services.ingestion_service import IngestionService

logger = logging.getLogger(__name__)


async def _run() -> int:
    loader = SupabaseStorageLoader(
        base_url=settings.supabase_url,
        api_key=settings.supabase_key,
        bucket=settings.supabase_bucket,
    )
    embedder = OpenAIEmbedder(
        api_key=settings.openai_api_key,
        model=settings.embedding_model,
    )
    store = PgVectorStore(
        dsn=settings.pgvector_dsn,
        top_k=settings.rag_top_k,
        similarity_threshold=settings.rag_similarity_threshold,
    )
    service = IngestionService(
        loader=loader,
        embedder=embedder,
        store=store,
        tokenizer=TiktokenEncoder(),
        role_scope=settings.rag_default_role_scope,
    )

    report = await service.reindex()
    logger.info(
        "Reporte: vistos=%d indexados=%d omitidos=%d fragmentos=%d",
        report.documents_seen,
        report.documents_indexed,
        report.documents_skipped,
        report.chunks_written,
    )
    if report.skipped:
        logger.info("Omitidos: %s", ", ".join(report.skipped))
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()

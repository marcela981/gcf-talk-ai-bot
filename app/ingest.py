"""Entrypoint CLI de ingestión del corpus RAG (ADR-010: on-demand, NO endpoint).

Uso:
    python -m app.ingest

Lee la tabla-catálogo `documents` (Supabase PostgREST, read-only — ADR-006-ter),
descarga cada documento público, extrae texto (PDF/txt/md), chunkea (tiktoken
cl100k_base, 500 tokens), embebe (text-embedding-3-small) y hace upsert
idempotente en pgvector.

Es el composition root de la ingestión: aquí —y solo aquí— se instancian los
adapters concretos y se inyectan en `IngestionService`. Mantiene el patrón
hexagonal: el servicio no conoce httpx, openai, psycopg ni pypdf.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from app.adapters.openai_embedder import OpenAIEmbedder
from app.adapters.pdf_text_extractor import PdfTextExtractor
from app.adapters.pgvector_store import PgVectorStore
from app.adapters.supabase_documents_table_loader import SupabaseDocumentsTableLoader
from app.adapters.tiktoken_encoder import TiktokenEncoder
from app.config import settings
from app.services.ingestion_service import IngestionService

logger = logging.getLogger(__name__)


async def _run() -> int:
    loader = SupabaseDocumentsTableLoader(
        base_url=settings.supabase_url,
        api_key=settings.supabase_key,
        levels=settings.rag_ingest_levels,
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
        extractor=PdfTextExtractor(),
        embedder=embedder,
        store=store,
        tokenizer=TiktokenEncoder(),
        # Fallback de scope solo si una fila no trae access_level; el loader de
        # tabla siempre lo informa, así que en la práctica no se usa.
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

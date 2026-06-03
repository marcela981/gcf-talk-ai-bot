"""Caso de uso de ingestión: loader -> extract -> chunk -> embed -> upsert.

Orquesta la indexación del corpus en el vector store. No conoce a FastAPI ni a
nc_py_api ni a los SDKs concretos: depende solo de puertos (`CorpusLoaderPort`,
`TextExtractorPort`, `EmbedderPort`, `VectorWritePort`) y del `Tokenizer` del
dominio.

Extracción de texto: delegada al `TextExtractorPort` (PDF vía pypdf + texto
plano), porque las reglas de capas prohíben importar SDKs externos aquí
(ARCHITECTURE.md §3). El servicio solo decide qué hacer con su resultado.

Resiliencia (ADR-006-ter): un fallo al DESCARGAR o EXTRAER un documento concreto
no aborta el lote — se registra, se cuenta como omitido y se continúa. Los
fallos de EMBED/UPSERT sí propagan: suelen ser sistémicos (OpenAI/DB caídos) y
enmascararlos ocultaría que el lote completo quedó sin indexar.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.domain.chunk import Chunk, EmbeddedChunk
from app.domain.chunking import Tokenizer, chunk_text
from app.services.corpus_loader_port import CorpusLoaderPort
from app.services.embedder_port import EmbedderPort
from app.services.retrieval_port import VectorWritePort
from app.services.text_extractor_port import TextExtractorPort

logger = logging.getLogger(__name__)


@dataclass
class IngestionReport:
    documents_seen: int = 0
    documents_indexed: int = 0
    documents_skipped: int = 0
    chunks_written: int = 0
    skipped: list[str] = field(default_factory=list)


class IngestionService:
    def __init__(
        self,
        *,
        loader: CorpusLoaderPort,
        extractor: TextExtractorPort,
        embedder: EmbedderPort,
        store: VectorWritePort,
        tokenizer: Tokenizer,
        role_scope: str,
        max_tokens: int = 500,
    ) -> None:
        self._loader = loader
        self._extractor = extractor
        self._embedder = embedder
        self._store = store
        self._tokenizer = tokenizer
        self._role_scope = role_scope
        self._max_tokens = max_tokens

    async def reindex(self) -> IngestionReport:
        """Recorre el corpus y reindexar cada documento. Idempotente por `source`."""
        report = IngestionReport()
        objects = await self._loader.list_documents()
        report.documents_seen = len(objects)

        for obj in objects:
            # `source` (cita ADR-013) puede diferir del `path` (locator de
            # descarga): en la tabla-catálogo path=URL y source=id.
            source = obj.source or obj.path
            try:
                data = await self._loader.download(obj.path)
                text = self._extractor.extract(obj.content_type, data)
            except Exception as exc:  # noqa: BLE001 — resiliencia deliberada
                report.documents_skipped += 1
                report.skipped.append(source)
                logger.warning(
                    "Omitido por error al descargar/extraer %s: %s", source, exc
                )
                continue

            if text is None:
                report.documents_skipped += 1
                report.skipped.append(source)
                logger.info(
                    "Omitido (tipo no soportado): %s [%s]", source, obj.content_type
                )
                continue

            role_scope = obj.role_scope or self._role_scope
            written = await self._index_document(source, text, role_scope)
            report.documents_indexed += 1
            report.chunks_written += written

        logger.info(
            "Ingestión completa: %d vistos, %d indexados, %d omitidos, %d fragmentos.",
            report.documents_seen,
            report.documents_indexed,
            report.documents_skipped,
            report.chunks_written,
        )
        return report

    async def _index_document(self, source: str, text: str, role_scope: str) -> int:
        fragments = chunk_text(text, tokenizer=self._tokenizer, max_tokens=self._max_tokens)
        if not fragments:
            # Documento vacío: reemplaza por nada (borra fragmentos previos).
            await self._store.upsert([])
            return 0

        vectors = await self._embedder.embed(fragments)
        embedded = [
            EmbeddedChunk(
                chunk=Chunk(
                    source=source,
                    chunk_id=i,
                    content=fragment,
                    role_scope=role_scope,
                ),
                embedding=vector,
            )
            for i, (fragment, vector) in enumerate(zip(fragments, vectors, strict=True))
        ]
        return await self._store.upsert(embedded)

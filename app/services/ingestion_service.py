"""Caso de uso de ingestión: loader -> chunk -> embed -> upsert (idempotente).

Orquesta la indexación del corpus en el vector store. No conoce a FastAPI ni a
nc_py_api ni a los SDKs concretos: depende solo de puertos (`CorpusLoaderPort`,
`EmbedderPort`, `VectorWritePort`) y del `Tokenizer` del dominio.

Extracción de texto: en esta fase solo se soportan tipos `text/*` (incluido
text/markdown), decodificados como UTF-8. Los binarios (PDF, DOCX, …) se omiten
con aviso.
PENDIENTE: extractor de PDF/DOCX (p. ej. pypdf) — no se añade una dependencia
no listada en el spec de forma silenciosa; el corpus de arranque es texto plano
o markdown.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.domain.chunk import Chunk, EmbeddedChunk
from app.domain.chunking import Tokenizer, chunk_text
from app.services.corpus_loader_port import CorpusLoaderPort, StoredObject
from app.services.embedder_port import EmbedderPort
from app.services.retrieval_port import VectorWritePort

logger = logging.getLogger(__name__)


@dataclass
class IngestionReport:
    documents_seen: int = 0
    documents_indexed: int = 0
    documents_skipped: int = 0
    chunks_written: int = 0
    skipped: list[str] = field(default_factory=list)


def _extract_text(obj: StoredObject, data: bytes) -> str | None:
    """Decodifica a texto los tipos soportados; ``None`` si no se soporta."""
    ctype = obj.content_type.split(";", 1)[0].strip().lower()
    if not (ctype.startswith("text/") or ctype in {"application/json"}):
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("No se pudo decodificar %s como UTF-8; se omite.", obj.path)
        return None


class IngestionService:
    def __init__(
        self,
        *,
        loader: CorpusLoaderPort,
        embedder: EmbedderPort,
        store: VectorWritePort,
        tokenizer: Tokenizer,
        role_scope: str,
        max_tokens: int = 500,
    ) -> None:
        self._loader = loader
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
            data = await self._loader.download(obj.path)
            text = _extract_text(obj, data)
            if text is None:
                report.documents_skipped += 1
                report.skipped.append(obj.path)
                logger.info("Omitido (tipo no soportado): %s [%s]", obj.path, obj.content_type)
                continue

            written = await self._index_document(obj.path, text)
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

    async def _index_document(self, source: str, text: str) -> int:
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
                    role_scope=self._role_scope,
                ),
                embedding=vector,
            )
            for i, (fragment, vector) in enumerate(zip(fragments, vectors, strict=True))
        ]
        return await self._store.upsert(embedded)

"""Tests del IngestionService con dobles que cumplen los Protocols (sin red)."""
from __future__ import annotations

import pytest

from app.domain.chunk import EmbeddedChunk
from app.services.corpus_loader_port import StoredObject
from app.services.ingestion_service import IngestionService


class CharTokenizer:
    def encode(self, text: str) -> list[int]:
        return [ord(c) for c in text]

    def decode(self, tokens: list[int]) -> str:
        return "".join(chr(t) for t in tokens)


class FakeLoader:
    def __init__(self, files: dict[str, tuple[str, bytes]]) -> None:
        self._files = files

    async def list_documents(self) -> list[StoredObject]:
        return [
            StoredObject(path=p, content_type=ct, size=len(data))
            for p, (ct, data) in self._files.items()
        ]

    async def download(self, path: str) -> bytes:
        return self._files[path][1]


class FakeExtractor:
    """Mimetiza al PdfTextExtractor real sin pypdf: text/* -> UTF-8, resto None."""

    def extract(self, content_type: str, data: bytes) -> str | None:
        ctype = content_type.split(";", 1)[0].strip().lower()
        if ctype.startswith("text/") or ctype == "application/json":
            return data.decode("utf-8")
        return None


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[float(len(t)), 0.0, 0.0] for t in texts]


class FakeStore:
    def __init__(self) -> None:
        self.upserts: list[list[EmbeddedChunk]] = []

    async def upsert(self, chunks: list[EmbeddedChunk]) -> int:
        self.upserts.append(chunks)
        return len(chunks)


@pytest.mark.asyncio
async def test_reindex_indexes_text_chunks_and_skips_binary():
    files = {
        "corporate/policy.md": ("text/markdown", b"abcdefghij"),  # 10 chars
        "corporate/scan.pdf": ("application/pdf", b"%PDF-1.4 binary"),
    }
    loader, embedder, store = FakeLoader(files), FakeEmbedder(), FakeStore()
    service = IngestionService(
        loader=loader,
        extractor=FakeExtractor(),
        embedder=embedder,
        store=store,
        tokenizer=CharTokenizer(),
        role_scope="corporate",
        max_tokens=4,
    )

    report = await service.reindex()

    assert report.documents_seen == 2
    assert report.documents_indexed == 1
    assert report.documents_skipped == 1
    assert report.skipped == ["corporate/scan.pdf"]
    assert report.chunks_written == 3  # 10 chars / 4 => 3 fragmentos

    written = store.upserts[0]
    assert all(isinstance(e, EmbeddedChunk) for e in written)
    assert [e.chunk.chunk_id for e in written] == [0, 1, 2]
    assert all(e.chunk.role_scope == "corporate" for e in written)
    assert all(e.chunk.source == "corporate/policy.md" for e in written)
    # cada fragmento embebido individualmente, en orden
    assert embedder.calls == [["abcd", "efgh", "ij"]]


@pytest.mark.asyncio
async def test_reindex_skips_failed_download_and_continues():
    # Una url muerta no debe abortar el lote: se omite y el resto se indexa.
    docs = [
        StoredObject(path="ok-1", content_type="text/plain", size=4,
                     role_scope="noroot", source="1"),
        StoredObject(path="dead", content_type="text/plain", size=4,
                     role_scope="noroot", source="2"),
        StoredObject(path="ok-2", content_type="text/plain", size=4,
                     role_scope="root", source="3"),
    ]
    contents = {"ok-1": b"aaaa", "ok-2": b"bbbb"}

    class _Loader:
        async def list_documents(self):
            return docs

        async def download(self, path):
            if path == "dead":
                raise RuntimeError("boom: url muerta")
            return contents[path]

    embedder, store = FakeEmbedder(), FakeStore()
    service = IngestionService(
        loader=_Loader(),
        extractor=FakeExtractor(),
        embedder=embedder,
        store=store,
        tokenizer=CharTokenizer(),
        role_scope="DEFAULT",
        max_tokens=100,
    )

    report = await service.reindex()  # no debe lanzar

    assert report.documents_seen == 3
    assert report.documents_indexed == 2
    assert report.documents_skipped == 1
    assert report.skipped == ["2"]  # se reporta por `source`, no por path


@pytest.mark.asyncio
async def test_reindex_preserves_literal_role_scope_per_document():
    docs = [
        StoredObject(path="a", content_type="text/plain", size=4,
                     role_scope="noroot", source="10"),
        StoredObject(path="b", content_type="text/plain", size=4,
                     role_scope="semiroot", source="11"),
        StoredObject(path="c", content_type="text/plain", size=4,
                     role_scope=None, source="12"),  # sin scope -> default del servicio
    ]
    contents = {"a": b"aaaa", "b": b"bbbb", "c": b"cccc"}

    class _Loader:
        async def list_documents(self):
            return docs

        async def download(self, path):
            return contents[path]

    store = FakeStore()
    service = IngestionService(
        loader=_Loader(),
        extractor=FakeExtractor(),
        embedder=FakeEmbedder(),
        store=store,
        tokenizer=CharTokenizer(),
        role_scope="DEFAULT",
        max_tokens=100,
    )

    await service.reindex()

    by_source = {u[0].chunk.source: u[0].chunk.role_scope for u in store.upserts}
    assert by_source == {"10": "noroot", "11": "semiroot", "12": "DEFAULT"}

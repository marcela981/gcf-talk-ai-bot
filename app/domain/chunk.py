"""Domain entities for the RAG corpus: a retrievable text fragment.

Pure data, no I/O. `Chunk` is the unit produced by chunking and returned by the
retrieval port; `EmbeddedChunk` couples a chunk with its vector for the write
(ingestion) side. Both are framework-agnostic so they can cross every layer.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """A fragment of a corporate document, scoped to a single role.

    * ``source``     — human-readable origin used for the citation required by
      ADR-013 (e.g. ``"politicas-rrhh.pdf"``).
    * ``chunk_id``   — ordinal of the fragment within ``source`` (0-based).
    * ``content``    — the fragment text.
    * ``role_scope`` — the role/group allowed to read this fragment. Retrieval
      filters on it PRE-query (see ``RetrievalPort.search``), never after.
    * ``score``      — similarity score in ``[0, 1]``, populated only when the
      chunk comes back from a similarity search; ``None`` at ingestion time.
    """

    source: str
    chunk_id: int
    content: str
    role_scope: str
    score: float | None = None


@dataclass(frozen=True)
class EmbeddedChunk:
    """A chunk paired with its embedding vector, ready to upsert into pgvector.

    Kept separate from ``Chunk`` so the retrieval read-path never has to carry
    the (large) vector around, and so the embedding concern stays out of the
    citation/display entity.
    """

    chunk: Chunk
    embedding: list[float]

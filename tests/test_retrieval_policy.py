"""Tests de la política de recuperación (umbral y top_k)."""
from __future__ import annotations

import pytest

from app.domain.chunk import Chunk
from app.domain.retrieval_policy import RetrievalPolicy


def _chunk(score: float | None, chunk_id: int = 0) -> Chunk:
    return Chunk(
        source="s.pdf",
        chunk_id=chunk_id,
        content="c",
        role_scope="corporate",
        score=score,
    )


def test_should_retrieve_ignores_blank_query():
    policy = RetrievalPolicy(top_k=4, similarity_threshold=0.75)
    assert policy.should_retrieve("   ") is False
    assert policy.should_retrieve("hola") is True


def test_select_filters_below_threshold_inclusive():
    policy = RetrievalPolicy(top_k=4, similarity_threshold=0.75)
    chunks = [_chunk(0.90, 0), _chunk(0.74, 1), _chunk(0.75, 2)]
    assert [c.chunk_id for c in policy.select(chunks)] == [0, 2]


def test_select_limits_top_k_preserving_order():
    policy = RetrievalPolicy(top_k=2, similarity_threshold=0.0)
    chunks = [_chunk(0.9, 0), _chunk(0.8, 1), _chunk(0.7, 2)]
    assert [c.chunk_id for c in policy.select(chunks)] == [0, 1]


def test_select_drops_chunks_without_score():
    policy = RetrievalPolicy(top_k=4, similarity_threshold=0.5)
    chunks = [_chunk(None, 0), _chunk(0.9, 1)]
    assert [c.chunk_id for c in policy.select(chunks)] == [1]


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        RetrievalPolicy(top_k=0, similarity_threshold=0.5)
    with pytest.raises(ValueError):
        RetrievalPolicy(top_k=1, similarity_threshold=1.5)

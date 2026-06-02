"""Tests del chunking puro (app.domain.chunking).

El tokenizador real (tiktoken) descarga su vocabulario por red en el primer uso,
así que aquí se inyecta un tokenizador determinista char-level: un token = un
codepoint. Es reversible y exacto (`decode(a)+decode(b) == decode(a+b)`), lo que
permite verificar el invariante de no-pérdida sin red.
"""
from __future__ import annotations

import pytest

from app.domain.chunking import chunk_text


class CharTokenizer:
    def encode(self, text: str) -> list[int]:
        return [ord(c) for c in text]

    def decode(self, tokens: list[int]) -> str:
        return "".join(chr(t) for t in tokens)


def test_empty_text_returns_no_chunks():
    assert chunk_text("", tokenizer=CharTokenizer()) == []


def test_text_shorter_than_max_is_single_chunk():
    assert chunk_text("hola", tokenizer=CharTokenizer(), max_tokens=500) == ["hola"]


def test_chunks_respect_max_tokens():
    tok = CharTokenizer()
    text = "a" * 1203
    chunks = chunk_text(text, tokenizer=tok, max_tokens=500)
    assert [len(c) for c in chunks] == [500, 500, 203]
    assert all(len(tok.encode(c)) <= 500 for c in chunks)


def test_no_content_loss_join_reconstructs_original():
    tok = CharTokenizer()
    text = "Política RRHH: vacaciones, permisos y más — €100.\nLínea 2 con acentos áéí."
    chunks = chunk_text(text, tokenizer=tok, max_tokens=7)
    assert "".join(chunks) == text  # sin pérdida, sin solapamiento, en orden


def test_invalid_max_tokens_raises():
    with pytest.raises(ValueError):
        chunk_text("x", tokenizer=CharTokenizer(), max_tokens=0)

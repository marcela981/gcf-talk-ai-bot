"""Tokenizador respaldado por tiktoken (encoding cl100k_base — ADR-009).

Implementa el Protocol `app.domain.chunking.Tokenizer`. Vive en la capa de
adapters precisamente porque importa una librería de terceros (`tiktoken`), lo
que `domain/` tiene prohibido. `tiktoken` además descarga su vocabulario por red
en el primer uso, así que mantenerlo fuera del dominio deja el chunking
testeable sin red (los tests inyectan un tokenizador falso).

El import de `tiktoken` es perezoso (dentro de `__init__`) para que importar
este módulo no exija la dependencia hasta que realmente se construya el encoder.
"""
from __future__ import annotations


class TiktokenEncoder:
    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        import tiktoken  # import perezoso: solo al construir el encoder real

        self._enc = tiktoken.get_encoding(encoding_name)

    def encode(self, text: str) -> list[int]:
        return self._enc.encode(text)

    def decode(self, tokens: list[int]) -> str:
        return self._enc.decode(tokens)

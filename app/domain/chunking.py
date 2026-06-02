"""Chunking de documentos del corpus. Lógica pura, sin I/O ni SDKs.

ADR-009 fija el chunking en 500 tokens con `tiktoken` (encoding cl100k_base).
Sin embargo, las reglas de import de ARCHITECTURE.md restringen `domain/` a la
stdlib: no puede importar `tiktoken` (una librería de terceros) ni ningún SDK.

Reconciliación: `chunk_text` es puro y recibe el `Tokenizer` por inyección.
El tokenizador real respaldado por tiktoken vive en
`app/adapters/tiktoken_encoder.py` (una capa donde sí se permiten dependencias
externas). Así esta función:

* respeta la restricción de capas (stdlib-only en domain),
* es 100 % testeable sin red (los tests inyectan un tokenizador falso
  determinista — tiktoken descarga su vocabulario por red en el primer uso),
* mantiene el invariante de ADR-009 cuando el adapter de producción la alimenta
  con el encoding cl100k_base.
"""
from __future__ import annotations

from typing import Protocol


class Tokenizer(Protocol):
    """Contrato mínimo de un tokenizador reversible.

    `decode(encode(text)) == text` y, de forma crítica para el chunking,
    `decode(a) + decode(b) == decode(a + b)` para sublistas contiguas de tokens:
    así la concatenación de los fragmentos reconstruye el texto sin pérdida.
    """

    def encode(self, text: str) -> list[int]: ...

    def decode(self, tokens: list[int]) -> str: ...


def chunk_text(
    text: str,
    *,
    tokenizer: Tokenizer,
    max_tokens: int = 500,
) -> list[str]:
    """Parte `text` en fragmentos de a lo sumo `max_tokens` tokens.

    Invariantes (verificados en tests):

    * cada fragmento mide ``<= max_tokens`` tokens según `tokenizer`;
    * ``"".join(chunk_text(t)) == t`` — sin pérdida, sin solapamiento, en orden;
    * texto vacío (o sólo separable a 0 tokens) devuelve ``[]``.

    El chunking es fijo (no por frase ni por solapamiento): ventanas contiguas
    de tamaño constante, según ADR-009.
    """
    if max_tokens <= 0:
        raise ValueError(f"max_tokens debe ser > 0; recibido {max_tokens!r}.")

    tokens = tokenizer.encode(text)
    if not tokens:
        return []

    chunks: list[str] = []
    for start in range(0, len(tokens), max_tokens):
        window = tokens[start : start + max_tokens]
        chunks.append(tokenizer.decode(window))
    return chunks

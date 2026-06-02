"""Política de recuperación: decide si recuperar y con qué top_k / threshold.

Lógica pura, sin I/O. Es el único lugar del dominio que conoce los números de
recuperación (top_k, umbral de similitud); los adapters los reciben por
inyección en el composition root, de modo que no se dupliquen como constantes
sueltas.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.chunk import Chunk


@dataclass(frozen=True)
class RetrievalPolicy:
    """Parámetros y reglas de la recuperación por similitud.

    * ``top_k``                — máximo de fragmentos a inyectar en el prompt.
    * ``similarity_threshold`` — similitud mínima [0, 1] para considerar
      pertinente un fragmento. Evita el antipatrón "sin umbral".
    """

    top_k: int
    similarity_threshold: float

    def __post_init__(self) -> None:
        if self.top_k <= 0:
            raise ValueError(f"top_k debe ser > 0; recibido {self.top_k!r}.")
        if not 0.0 <= self.similarity_threshold <= 1.0:
            raise ValueError(
                "similarity_threshold debe estar en [0, 1]; recibido "
                f"{self.similarity_threshold!r}."
            )

    def should_retrieve(self, query: str) -> bool:
        """En Fase 2 se recupera siempre que haya una consulta no vacía."""
        return bool(query.strip())

    def select(self, chunks: list[Chunk]) -> list[Chunk]:
        """Aplica umbral y top_k preservando el orden de entrada.

        El store ya filtra por umbral y limita por top_k en SQL; este método es
        la misma regla expresada en el dominio (defensa en profundidad y unidad
        de prueba sin base de datos). Descarta fragmentos sin score.
        """
        kept = [
            c
            for c in chunks
            if c.score is not None and c.score >= self.similarity_threshold
        ]
        return kept[: self.top_k]

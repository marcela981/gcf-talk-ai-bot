"""Puerto (interfaz) para generar embeddings. Implementaciones en adapters/."""
from __future__ import annotations

from typing import Protocol


class EmbedderPort(Protocol):
    """Genera vectores de embedding para una lista de textos.

    Devuelve una lista de vectores en el mismo orden que `texts`. La dimensión
    la fija el modelo (ADR-008: text-embedding-3-small => 1536). Se usa tanto en
    ingestión (lote de fragmentos) como en la consulta (un solo texto).
    """

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

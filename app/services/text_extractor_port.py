"""Puerto para extraer texto plano del contenido binario de un documento.

ADR-006-ter: la extracción de PDF requiere una librería externa (`pypdf`). Las
reglas de capas (ARCHITECTURE.md §3) prohíben que `services/` importe SDKs
externos, así que la extracción se modela como un puerto cuyo adapter
(`adapters/pdf_text_extractor.py`) encapsula la dependencia. El servicio depende
solo de este contrato.

Contrato:
- ``extract`` es SÍNCRONO: es trabajo CPU-bound (parseo), sin I/O.
- Devuelve ``None`` cuando el ``content_type`` NO se soporta (omisión limpia, no
  un error).
- Puede LANZAR si el contenido está corrupto o mal codificado; el llamador
  (IngestionService) captura la excepción, la registra y continúa con el resto
  del lote (resiliencia ante fallos individuales).
"""
from __future__ import annotations

from typing import Protocol


class TextExtractorPort(Protocol):
    def extract(self, content_type: str, data: bytes) -> str | None:
        """Devuelve el texto del documento, o ``None`` si el tipo no se soporta."""
        ...

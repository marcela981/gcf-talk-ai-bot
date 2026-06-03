"""Extractor de texto para el corpus: PDF (pypdf) y texto plano (txt/md/json).

Implementa `TextExtractorPort`. Encapsula `pypdf` para que la dependencia no
suba a la capa `services/` (ARCHITECTURE.md §3). Pese al nombre histórico, no
solo maneja PDF: los tipos `text/*` y `application/json` se decodifican como
UTF-8 sin tocar pypdf.

Política de errores (ver TextExtractorPort):
- Tipo no soportado (p. ej. DOCX) ⇒ ``None`` (omisión limpia).
- PDF corrupto / no decodificable ⇒ se LANZA; IngestionService lo captura,
  cuenta el documento como omitido y sigue con el lote.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


class PdfTextExtractor:
    def extract(self, content_type: str, data: bytes) -> str | None:
        ctype = content_type.split(";", 1)[0].strip().lower()

        if ctype == "application/pdf":
            return self._extract_pdf(data)

        if ctype.startswith("text/") or ctype == "application/json":
            # Puede lanzar UnicodeDecodeError -> lo captura el servicio.
            return data.decode("utf-8")

        logger.info("Tipo no soportado para extracción: %s", ctype)
        return None

    @staticmethod
    def _extract_pdf(data: bytes) -> str:
        # Import perezoso: el dominio/servicio nunca importa pypdf; solo este
        # adapter, y solo cuando hay un PDF que extraer.
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        parts = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(parts).strip()

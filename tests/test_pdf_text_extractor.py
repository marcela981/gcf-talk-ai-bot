"""Tests del PdfTextExtractor (pypdf + texto plano), sin red."""
from __future__ import annotations

import io

import pytest

from app.adapters.pdf_text_extractor import PdfTextExtractor


def test_decodes_plain_text_and_markdown_as_utf8():
    ext = PdfTextExtractor()
    assert ext.extract("text/plain", b"hola mundo") == "hola mundo"
    assert ext.extract("text/markdown; charset=utf-8", b"# titulo") == "# titulo"
    assert ext.extract("application/json", b'{"a": 1}') == '{"a": 1}'


def test_unsupported_type_returns_none():
    ext = PdfTextExtractor()
    # DOCX y binarios desconocidos -> omisión limpia (None), no excepción.
    assert ext.extract(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        b"PK\x03\x04",
    ) is None
    assert ext.extract("application/octet-stream", b"\x00\x01") is None


def test_valid_pdf_is_parsed_without_error():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)

    ext = PdfTextExtractor()
    # Página en blanco: sin texto, pero la rama PDF debe procesarla sin lanzar.
    assert ext.extract("application/pdf", buf.getvalue()) == ""


def test_corrupt_pdf_raises_so_service_can_skip_it():
    ext = PdfTextExtractor()
    with pytest.raises(Exception):  # noqa: B017 — pypdf lanza tipos varios
        ext.extract("application/pdf", b"%PDF-1.4 esto no es un pdf valido")

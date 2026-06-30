"""Unit tests for app.domain.time_context (Bloque 2.1): ancla de fecha del prompt.

Función pura: recibe un ``now`` aware (lo calcula la capa de servicio con el reloj)
y devuelve la línea legible que se inyecta en el contexto del LLM. Sin reloj real:
se pasa un instante fijo, así el test es determinista.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.domain.time_context import current_datetime_block

BOGOTA = ZoneInfo("America/Bogota")


def test_block_is_legible_and_localized():
    line = current_datetime_block(datetime(2026, 6, 30, 14, 30, tzinfo=BOGOTA))

    assert line.startswith("Fecha y hora actuales:")
    assert "martes 30 de junio de 2026" in line  # día/mes en español, sin locale
    assert "14:30" in line
    assert "(America/Bogota)" in line


def test_block_anchors_relative_dates():
    line = current_datetime_block(datetime(2026, 6, 30, 14, 30, tzinfo=BOGOTA))

    # Instruye explícitamente a anclar fechas relativas, no a deducir del modelo.
    assert "mañana" in line
    assert "ancla" in line.lower()


def test_utc_label_fallback_for_plain_timezone():
    line = current_datetime_block(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc))

    assert "(UTC)" in line
    assert "jueves 1 de enero de 2026" in line

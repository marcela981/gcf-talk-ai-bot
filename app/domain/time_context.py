"""Bloque de contexto temporal para el prompt (dominio puro, sin reloj).

El LLM no conoce la fecha real: sin un ancla alucina (p. ej. resuelve 'hoy' a una
fecha de su corpus de entrenamiento — el bug del smoke "hoy es 1 de noviembre de
2023"). Esta función formatea un instante YA calculado por el llamador
(``datetime.now(tz)`` en la capa de servicio) como una línea legible para inyectar
en el slot L1/L2 del system prompt (NO en L0 inmutable). Es PURA: recibe el ``now``
aware y no lee el reloj, así se testea de forma determinista.
"""
from __future__ import annotations

from datetime import datetime

_DIAS = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)
_MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def current_datetime_block(now: datetime) -> str:
    """Línea de contexto con la fecha/hora actuales en la zona de ``now`` (aware).

    Formato: ``Fecha y hora actuales: viernes 30 de junio de 2026, 14:30
    (America/Bogota). ...``. El llamador pasa ``now`` ya localizado en la zona del
    usuario; el modelo la usa como ancla para resolver fechas relativas y deja de
    alucinar la fecha. Los nombres de día/mes se escriben a mano (sin depender del
    locale del sistema, que es poco fiable en contenedores).
    """
    dia = _DIAS[now.weekday()]
    mes = _MESES[now.month - 1]
    zona = getattr(now.tzinfo, "key", None) or now.strftime("%Z") or "UTC"
    return (
        f"Fecha y hora actuales: {dia} {now.day} de {mes} de {now.year}, "
        f"{now:%H:%M} ({zona}). Úsala como ancla para resolver expresiones de "
        f"fecha relativas (p. ej. 'hoy', 'mañana', 'el viernes'); no la deduzcas "
        f"de tu conocimiento previo."
    )

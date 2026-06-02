"""Ensamblado del bloque de contexto recuperado (slot L2). Lógica pura.

Convierte los `Chunk` recuperados en un único mensaje de sistema adicional que
`prompt_builder.build_messages` colocará en el slot L2 vía `extra_system`. No
toca ni conoce L0.

El bloque incluye la instrucción de citar la fuente (ADR-013) y reafirma, de
forma subordinada a L0, que el modelo no debe inventar: si el contexto no es
pertinente, debe ignorarlo. Esto NO sustituye a L0 — las reglas inviolables del
core siguen teniendo prioridad por construcción del prompt.
"""
from __future__ import annotations

from app.domain.chunk import Chunk

_INSTRUCTION = (
    "CONTEXTO CORPORATIVO RECUPERADO (capa L2, subordinada a tus reglas "
    "inviolables).\n"
    "Úsalo solo si es pertinente para la consulta del usuario. Cuando lo uses, "
    "cita la fuente entre comillas invertidas, por ejemplo: "
    "\"Según `politicas-rrhh.pdf`, …\". "
    "Si el contexto no responde a la consulta, ignóralo y NO lo menciones; "
    "nunca inventes datos que no estén aquí ni en lo que el usuario aporte."
)


def assemble_context_block(chunks: list[Chunk]) -> str | None:
    """Devuelve el texto del bloque L2, o ``None`` si no hay fragmentos.

    Cada fragmento se rotula con su fuente para que el modelo pueda citarla
    (ADR-013). Los fragmentos van en el orden recibido (ya ordenado por
    similitud por el store + la política).
    """
    if not chunks:
        return None

    parts = [_INSTRUCTION]
    for chunk in chunks:
        parts.append(f"[Fuente: {chunk.source}]\n{chunk.content}")
    return "\n\n".join(parts)

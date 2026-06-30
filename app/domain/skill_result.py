"""Result of executing a skill (ADR-018). Pure value object, no I/O.

Encapsula el desenlace de un :meth:`~app.services.skill.Skill.execute`: éxito con
datos, o fallo con un mensaje. El loop de ADR-017 lo convierte en el *contenido*
de un turno de herramienta (:class:`~app.domain.tool_calling.ToolResultTurn`) que
el modelo lee en la siguiente iteración.

El error es **dato**, no excepción: un fallo de skill (argumento inválido, sin
resultados, error de I/O atrapado por el loop) vuelve al modelo como un resultado
legible para que pueda recuperarse o explicarle al usuario — nunca tumba el bucle.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SkillResult:
    """Éxito (``ok=True``, ``data``) o fallo (``ok=False``, ``error``).

    Usa los constructores :meth:`success` / :meth:`failure` en vez del ``__init__``
    directo para no dejar estados ambiguos (p. ej. ``ok=True`` con ``error``).
    """

    ok: bool
    data: Any = None
    error: str | None = None

    @classmethod
    def success(cls, data: Any = None) -> "SkillResult":
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, error: str) -> "SkillResult":
        return cls(ok=False, error=error)

    def to_tool_content(self) -> str:
        """Render como string para el turno de herramienta que consume el LLM.

        JSON con ``ensure_ascii=False`` para que el español viaje legible; ``default=str``
        evita reventar ante valores no serializables (degrada a su repr). El sobre
        ``{"ok": ...}`` le da al modelo una señal explícita de éxito/fallo.
        """
        if self.ok:
            payload: dict[str, Any] = {"ok": True, "data": self.data}
        else:
            payload = {"ok": False, "error": self.error}
        return json.dumps(payload, ensure_ascii=False, default=str)

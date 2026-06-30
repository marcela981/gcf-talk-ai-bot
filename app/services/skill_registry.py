"""`SkillRegistry` (ADR-018): catálogo ``name -> Skill`` (Registry).

Tres responsabilidades, todas neutrales respecto al proveedor:

1. **Registrar** skills (en el composition root, ``app/main.py``).
2. **Exponer** los `ToolSpec` (JSON-schemas) que el motor de ADR-017 entrega al
   LLM — *neutrales*: el adapter del LLM los traduce al wire del proveedor.
3. **Resolver** una skill por nombre al ejecutar una tool-call.

No conoce el `LLMPort` ni FastAPI: es un componente de ``services`` puro sobre el
contrato `Skill`. El motor consulta `tool_specs()` y `get(name)`; alta de skill =
`register(...)` aquí, sin tocar el motor (OCP).
"""
from __future__ import annotations

from app.domain.tool_calling import ToolSpec
from app.services.skill import Skill


class SkillRegistry:
    """Catálogo central de skills, indexado por ``name``."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """Registra una skill. Falla si su ``name`` ya está tomado.

        El nombre duplicado es un error de wiring (dos skills compartirían la
        misma tool ante el LLM): se rechaza explícito en vez de pisar en silencio.
        """
        if skill.name in self._skills:
            raise ValueError(f"Skill ya registrada con name {skill.name!r}.")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        """Resuelve una skill por nombre. Levanta ``KeyError`` si no existe."""
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"No hay skill registrada con name {name!r}.") from exc

    def tool_specs(self) -> list[ToolSpec]:
        """Los `ToolSpec` de todas las skills, para ofrecer al LLM (ADR-017)."""
        return [
            ToolSpec(
                name=skill.name,
                description=skill.description,
                parameters_schema=skill.parameters_schema,
            )
            for skill in self._skills.values()
        ]

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: object) -> bool:
        return name in self._skills

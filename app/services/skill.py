"""Contrato `Skill` (ADR-018): la unidad extensible de capacidad del agente.

Interfaz **sin dependencias de framework** (Protocol estructural). Cada skill es
una *estrategia* (Strategy) intercambiable tras esta interfaz; el motor de ADR-017
la ve como una *tool* (su ``name``/``description``/``parameters_schema`` se exponen
al LLM) y la ejecuta como un *comando* (``args`` + ``execute`` ⇒ ``SkillResult``).

Regla de capas (ARCHITECTURE §3): el **contrato** vive en ``services``; las skills
concretas que tocan infraestructura (vector store, CRM, Deck…) son **adapters**.
Alta de una skill = nueva clase + registro en el composition root; **no** se toca
el motor, el `LLMPort`, ni `ConversationService` (OCP).
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.domain.actor_context import ActorContext
from app.domain.skill_result import SkillResult


@runtime_checkable
class Skill(Protocol):
    """Una capacidad invocable por el LLM vía tool-calling.

    * ``name``              — identificador único; **es el nombre de la tool** ante
      el LLM y la clave de resolución en el :class:`SkillRegistry`.
    * ``description``       — prosa que el LLM usa para **decidir cuándo** invocarla.
    * ``parameters_schema`` — **JSON-schema** de los argumentos; el motor lo entrega
      al modelo y el proveedor valida contra él los argumentos emitidos. Es
      **contrato público**: cambiarlo altera cómo el LLM invoca la skill.
    * ``execute``           — ejecuta la acción con los ``args`` (ya parseados) bajo
      la identidad ``actor`` (resuelta, ADR-016) y devuelve un ``SkillResult``.

    ``execute`` es ``async``: las skills de infraestructura hacen I/O (red, BD).
    Mantenerla delgada y delegar el I/O a puertos evita skills "gordas" (ADR-018).
    """

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def parameters_schema(self) -> dict[str, Any]: ...

    async def execute(self, args: dict[str, Any], actor: ActorContext) -> SkillResult: ...

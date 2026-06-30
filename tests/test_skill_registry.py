"""Unit tests for app.services.skill_registry (ADR-018).

Las skills son dobles que satisfacen el Protocol `Skill` estructuralmente — sin
red, sin LLM. Verifican registro, resolución por nombre y la exposición neutral
de `ToolSpec` para el motor.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domain.actor_context import ActorContext
from app.domain.skill_result import SkillResult
from app.domain.tool_calling import ToolSpec
from app.services.skill_registry import SkillRegistry


class FakeSkill:
    def __init__(
        self,
        name: str,
        *,
        description: str = "una skill falsa",
        schema: dict[str, Any] | None = None,
        result: SkillResult | None = None,
    ) -> None:
        self._name = name
        self._description = description
        self._schema = schema or {"type": "object", "properties": {}}
        self._result = result or SkillResult.success({"ok": True})
        self.calls: list[tuple[dict[str, Any], ActorContext]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self._schema

    async def execute(self, args: dict[str, Any], actor: ActorContext) -> SkillResult:
        self.calls.append((args, actor))
        return self._result


def test_register_and_get_by_name():
    registry = SkillRegistry()
    skill = FakeSkill("buscar")

    registry.register(skill)

    assert registry.get("buscar") is skill
    assert "buscar" in registry
    assert len(registry) == 1


def test_duplicate_name_is_rejected():
    registry = SkillRegistry()
    registry.register(FakeSkill("buscar"))

    with pytest.raises(ValueError, match="ya registrada"):
        registry.register(FakeSkill("buscar"))


def test_get_unknown_raises_keyerror():
    registry = SkillRegistry()

    with pytest.raises(KeyError, match="No hay skill"):
        registry.get("inexistente")


def test_tool_specs_exposes_neutral_schemas_for_the_engine():
    registry = SkillRegistry()
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    }
    registry.register(FakeSkill("buscar", description="busca cosas", schema=schema))
    registry.register(FakeSkill("contar"))

    specs = registry.tool_specs()

    assert all(isinstance(s, ToolSpec) for s in specs)
    by_name = {s.name: s for s in specs}
    assert set(by_name) == {"buscar", "contar"}
    assert by_name["buscar"].description == "busca cosas"
    assert by_name["buscar"].parameters_schema == schema


def test_empty_registry_has_no_specs_and_zero_length():
    registry = SkillRegistry()

    assert registry.tool_specs() == []
    assert len(registry) == 0
    assert "cualquiera" not in registry

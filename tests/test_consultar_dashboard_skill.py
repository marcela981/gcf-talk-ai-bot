"""Unit tests para ConsultarDashboardSkill (lectura del dashboard, ADR-020/021/023, Bloque 3).

El `DashboardPort` se reemplaza por un `FakeDashboard` — sin BD. Se verifica que la skill:
se REHÚSA sin identidad (uid None), delega en el port para 'tareas' y 'horas' (con rango de
fechas), suma las horas, valida el 'recurso' y las fechas, y convierte tanto un `uid` sin
perfil como una excepción del port en `SkillResult.failure` (dato, no excepción).
"""
from __future__ import annotations

import pytest

from app.adapters.consultar_dashboard_skill import ConsultarDashboardSkill
from app.adapters.dashboard_mysql_adapter import DashboardError, NoDashboardProfileError
from app.domain.actor_context import ActorContext
from app.domain.dashboard import DashboardTask, TimeLog

_USER = ActorContext(actor_id="users/mmazo", token="room1", impersonated_uid="mmazo")
_GUEST = ActorContext(actor_id="guests/abc", token="room1", impersonated_uid=None)


class FakeDashboard:
    def __init__(self, tasks=(), logs=(), error: Exception | None = None) -> None:
        self._tasks = list(tasks)
        self._logs = list(logs)
        self._error = error
        self.calls: list[tuple] = []

    async def list_tasks(self, uid):
        self.calls.append(("list_tasks", uid))
        if self._error:
            raise self._error
        return list(self._tasks)

    async def list_time_logs(self, uid, *, since=None, until=None):
        self.calls.append(("list_time_logs", uid, since, until))
        if self._error:
            raise self._error
        return list(self._logs)


@pytest.mark.asyncio
async def test_refuses_without_local_identity():
    dash = FakeDashboard(tasks=[DashboardTask(1, "X")])
    skill = ConsultarDashboardSkill(dashboard=dash)

    result = await skill.execute({"recurso": "tareas"}, _GUEST)

    assert not result.ok
    assert "invitados" in result.error
    assert dash.calls == []


@pytest.mark.asyncio
async def test_tareas_delegates_and_presents():
    dash = FakeDashboard(
        tasks=[
            DashboardTask(1, "Diseñar API", "open", "2026-07-10"),
            DashboardTask(2, "Revisar PR", "done", None),
        ]
    )
    skill = ConsultarDashboardSkill(dashboard=dash)

    result = await skill.execute({"recurso": "tareas"}, _USER)

    assert result.ok
    assert result.data["recurso"] == "tareas"
    assert result.data["total"] == 2
    assert result.data["tareas"][0] == {
        "titulo": "Diseñar API",
        "estado": "open",
        "vence": "2026-07-10",
    }
    assert dash.calls == [("list_tasks", "mmazo")]


@pytest.mark.asyncio
async def test_horas_delegates_with_range_and_sums():
    dash = FakeDashboard(
        logs=[
            TimeLog(11, "2026-07-05", 3.5, None),
            TimeLog(12, "2026-07-06", 4.0, "cliente"),
        ]
    )
    skill = ConsultarDashboardSkill(dashboard=dash)

    result = await skill.execute(
        {"recurso": "horas", "desde": "2026-07-01", "hasta": "2026-07-31"}, _USER
    )

    assert result.ok
    assert result.data["recurso"] == "horas"
    assert result.data["desde"] == "2026-07-01" and result.data["hasta"] == "2026-07-31"
    assert result.data["total_horas"] == 7.5
    assert len(result.data["registros"]) == 2
    assert dash.calls == [("list_time_logs", "mmazo", "2026-07-01", "2026-07-31")]


@pytest.mark.asyncio
async def test_horas_without_range_passes_none():
    dash = FakeDashboard(logs=[TimeLog(11, "2026-07-05", 2.0, None)])
    skill = ConsultarDashboardSkill(dashboard=dash)

    result = await skill.execute({"recurso": "horas"}, _USER)

    assert result.ok
    assert result.data["total_horas"] == 2.0
    assert dash.calls == [("list_time_logs", "mmazo", None, None)]


@pytest.mark.asyncio
async def test_invalid_recurso_is_failure():
    dash = FakeDashboard()
    skill = ConsultarDashboardSkill(dashboard=dash)

    result = await skill.execute({"recurso": "desempeño"}, _USER)

    assert not result.ok
    assert "tareas" in result.error and "horas" in result.error
    assert dash.calls == []


@pytest.mark.asyncio
async def test_invalid_date_is_failure():
    dash = FakeDashboard()
    skill = ConsultarDashboardSkill(dashboard=dash)

    result = await skill.execute({"recurso": "horas", "desde": "01/07/2026"}, _USER)

    assert not result.ok
    assert "desde" in result.error
    assert dash.calls == []


@pytest.mark.asyncio
async def test_inverted_range_is_failure():
    dash = FakeDashboard()
    skill = ConsultarDashboardSkill(dashboard=dash)

    result = await skill.execute(
        {"recurso": "horas", "desde": "2026-07-31", "hasta": "2026-07-01"}, _USER
    )

    assert not result.ok
    assert "rango" in result.error.lower()
    assert dash.calls == []


@pytest.mark.asyncio
async def test_no_profile_becomes_clear_failure():
    dash = FakeDashboard(
        error=NoDashboardProfileError("El usuario 'mmazo' no tiene perfil en el dashboard.")
    )
    skill = ConsultarDashboardSkill(dashboard=dash)

    result = await skill.execute({"recurso": "tareas"}, _USER)

    assert not result.ok
    assert "perfil" in result.error


@pytest.mark.asyncio
async def test_port_error_becomes_failure():
    dash = FakeDashboard(error=DashboardError("conexión caída"))
    skill = ConsultarDashboardSkill(dashboard=dash)

    result = await skill.execute({"recurso": "horas"}, _USER)

    assert not result.ok
    assert "dashboard" in result.error.lower()


def test_tool_schema_is_public_contract():
    skill = ConsultarDashboardSkill(dashboard=FakeDashboard())

    assert skill.name == "consultar_dashboard"
    schema = skill.parameters_schema
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["recurso"]
    assert schema["properties"]["recurso"]["enum"] == ["tareas", "horas"]
    for key in ("recurso", "desde", "hasta"):
        assert key in schema["properties"]

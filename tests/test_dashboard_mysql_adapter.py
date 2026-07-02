"""Unit tests para DashboardMySQLAdapter (Bloque 3), SIN BD.

Se inyecta un `fetch` fake (en vez de un driver MySQL) que responde con fixtures del
esquema real (`users.nc_user_id`/`id`, `tasks`, `time_logs`) y **registra cada query**.
Se verifica la REGLA DE ORO de ADR-021: toda query lleva el filtro de identidad
(`nc_user_id` en la resolución, `user_id`/`assigned_to` en las de datos), NO existe SELECT
sin ese filtro, el `uid` sin fila en `users` se rehúsa, y el uid vacío se rechaza. Estilo
de los tests de Calendar/Deck.
"""
from __future__ import annotations

import pytest

from app.adapters.dashboard_mysql_adapter import (
    DashboardError,
    DashboardMySQLAdapter,
    NoDashboardProfileError,
)

_USERS = [
    {"id": 7, "nc_user_id": "mmazo"},
    {"id": 8, "nc_user_id": "jdoe"},
]
_TASKS = [
    {"id": 1, "title": "Mía A", "status": "open", "due_date": "2026-07-10", "assigned_to": 7},
    {"id": 2, "title": "Mía B", "status": "done", "due_date": None, "assigned_to": 7},
    {"id": 3, "title": "De jdoe", "status": "open", "due_date": "2026-07-01", "assigned_to": 8},
]
_TIME_LOGS = [
    {"id": 10, "user_id": 7, "log_date": "2026-07-01", "hours": 4.0, "description": "a"},
    {"id": 11, "user_id": 7, "log_date": "2026-07-05", "hours": 3.5, "description": None},
    {"id": 12, "user_id": 8, "log_date": "2026-07-05", "hours": 9.0, "description": "otro"},
]


class FakeDb:
    """Fake de `fetch(sql, params)`: filtra las fixtures como lo haría el WHERE real."""

    def __init__(self, users=_USERS, tasks=_TASKS, time_logs=_TIME_LOGS) -> None:
        self.users = list(users)
        self.tasks = list(tasks)
        self.time_logs = list(time_logs)
        self.queries: list[tuple[str, dict]] = []

    async def fetch(self, sql: str, params: dict) -> list[dict]:
        self.queries.append((sql, params))
        low = sql.lower()
        if "from users" in low:
            return [{"id": u["id"]} for u in self.users if u["nc_user_id"] == params.get("uid")]
        if "from tasks" in low:
            uid = params.get("user_id")
            return [dict(t) for t in self.tasks if t["assigned_to"] == uid]
        if "from time_logs" in low:
            uid = params.get("user_id")
            rows = [dict(t) for t in self.time_logs if t["user_id"] == uid]
            if params.get("since"):
                rows = [r for r in rows if r["log_date"] >= params["since"]]
            if params.get("until"):
                rows = [r for r in rows if r["log_date"] <= params["until"]]
            return rows
        raise AssertionError(f"query inesperada: {sql}")


def _adapter(db: FakeDb) -> DashboardMySQLAdapter:
    return DashboardMySQLAdapter(
        host="db-tunnel",
        port=3306,
        name="dashboard_db",
        user="gcf_bot_ro",
        password="secret",
        fetch=db.fetch,
    )


def _assert_every_query_filters_by_identity(queries: list[tuple[str, dict]]) -> None:
    """REGLA DE ORO (ADR-021): NINGÚN SELECT sin filtro de identidad.

    Cada query debe llevar un WHERE que **bind-ee el parámetro de identidad**: `%(uid)s`
    en la resolución (`nc_user_id`) o `%(user_id)s` en las de datos (la columna concreta —
    `assigned_to`/`user_id`/`owner_id`— es detalle del esquema, ADR-021).
    """
    assert queries, "no se ejecutó ninguna query"
    for sql, params in queries:
        assert "where" in sql.lower(), f"query sin WHERE: {sql}"
        binds_uid = "%(uid)s" in sql and params.get("uid")
        binds_user_id = "%(user_id)s" in sql and "user_id" in params
        assert binds_uid or binds_user_id, f"query sin filtro de identidad: {sql} / {params}"


@pytest.mark.asyncio
async def test_list_tasks_filters_by_resolved_user_id():
    db = FakeDb()

    tasks = await _adapter(db).list_tasks("mmazo")

    # Solo las tareas de users.id=7 (mmazo); nunca las de jdoe.
    assert [t.id for t in tasks] == [1, 2]
    assert all(t.title.startswith("Mía") for t in tasks)
    # Primero se resolvió la identidad, luego se consultó con user_id=7.
    assert db.queries[0][1] == {"uid": "mmazo"}
    assert db.queries[1][1] == {"user_id": 7}
    _assert_every_query_filters_by_identity(db.queries)


@pytest.mark.asyncio
async def test_list_time_logs_filters_by_identity_and_date_range():
    db = FakeDb()

    logs = await _adapter(db).list_time_logs("mmazo", since="2026-07-02", until="2026-07-31")

    # id 10 (01-jul) queda fuera del rango; id 12 es de jdoe → excluido por identidad.
    assert [log.id for log in logs] == [11]
    assert logs[0].hours == 3.5
    # El filtro de identidad (user_id) SIEMPRE va, además del rango de fechas.
    data_query = db.queries[1]
    assert data_query[1]["user_id"] == 7
    assert data_query[1]["since"] == "2026-07-02" and data_query[1]["until"] == "2026-07-31"
    _assert_every_query_filters_by_identity(db.queries)


@pytest.mark.asyncio
async def test_time_logs_without_range_still_filters_by_identity():
    db = FakeDb()

    logs = await _adapter(db).list_time_logs("mmazo")

    assert [log.id for log in logs] == [10, 11]  # ambas de mmazo, ninguna de jdoe
    _assert_every_query_filters_by_identity(db.queries)


@pytest.mark.asyncio
async def test_uid_without_profile_is_refused():
    db = FakeDb()

    with pytest.raises(NoDashboardProfileError, match="perfil"):
        await _adapter(db).list_tasks("desconocido")

    # No debe ejecutarse ninguna query de datos: solo la de resolución (que no encontró).
    assert [q for q in db.queries if "from users" not in q[0].lower()] == []


@pytest.mark.asyncio
async def test_empty_uid_is_rejected():
    db = FakeDb()

    with pytest.raises(DashboardError):
        await _adapter(db).list_tasks("")

    assert db.queries == []  # ni siquiera se intenta resolver


def test_missing_config_rejected_at_construction():
    with pytest.raises(DashboardError):
        DashboardMySQLAdapter(
            host="", port=3306, name="d", user="u", password="p"
        )
    with pytest.raises(DashboardError):
        DashboardMySQLAdapter(
            host="db-tunnel", port=3306, name="d", user="u", password=""
        )

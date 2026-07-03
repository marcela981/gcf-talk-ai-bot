"""Unit tests para DashboardMySQLAdapter (Bloque 3), SIN BD.

Se inyecta un `fetch` fake (en vez de un driver MySQL) que responde con fixtures del
**esquema REAL** (`users.nc_user_id`/`id`, `tasks`, `activities`) y **registra cada query**.
Se verifica la REGLA DE ORO de ADR-021: toda query lleva el filtro de identidad
(`nc_user_id` en la resolución, `(owner_id OR assigned_to)` en las de datos), NO existe
SELECT sin ese filtro, se excluyen los borrados (`deleted_at IS NULL`), el `uid` sin fila en
`users` se rehúsa, y el uid vacío se rechaza. Estilo de los tests de Calendar/Deck.
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
# Columnas REALES de `tasks`: column_status (no 'status'), deadline (no 'due_date'),
# owner_id/assigned_to, deleted_at.
_TASKS = [
    {"id": 1, "title": "Diseñar API", "column_status": "in_progress",
     "deadline": "2026-07-10", "owner_id": 7, "assigned_to": 7, "deleted_at": None},
    # owner distinto pero asignada a 7 ⇒ entra por assigned_to.
    {"id": 2, "title": "Revisar PR", "column_status": "todo",
     "deadline": None, "owner_id": 9, "assigned_to": 7, "deleted_at": None},
    # de jdoe (8) ⇒ NO debe verse.
    {"id": 3, "title": "De jdoe", "column_status": "done",
     "deadline": "2026-07-01", "owner_id": 8, "assigned_to": 8, "deleted_at": None},
    # borrada de 7 ⇒ excluida por deleted_at.
    {"id": 4, "title": "Borrada", "column_status": "todo",
     "deadline": None, "owner_id": 7, "assigned_to": 7, "deleted_at": "2026-06-30 10:00:00"},
]
# Columnas REALES de `activities`: time_spent, start_date, completed_at, progress.
_ACTIVITIES = [
    {"id": 10, "title": "Reunión", "time_spent": 1.5, "start_date": "2026-07-01",
     "completed_at": "2026-07-01", "progress": 100, "owner_id": 7, "assigned_to": 7,
     "deleted_at": None},
    {"id": 11, "title": "Desarrollo", "time_spent": 3.0, "start_date": "2026-07-05",
     "completed_at": None, "progress": 40, "owner_id": 9, "assigned_to": 7,
     "deleted_at": None},
    {"id": 12, "title": "De jdoe", "time_spent": 9.0, "start_date": "2026-07-05",
     "completed_at": None, "progress": 0, "owner_id": 8, "assigned_to": 8,
     "deleted_at": None},
    {"id": 13, "title": "Borrada", "time_spent": 5.0, "start_date": "2026-07-05",
     "completed_at": None, "progress": 0, "owner_id": 7, "assigned_to": 7,
     "deleted_at": "2026-06-20 09:00:00"},
]


class FakeDb:
    """Fake de `fetch(sql, params)`: filtra las fixtures como el WHERE real (identidad + no-borrados)."""

    def __init__(self, users=_USERS, tasks=_TASKS, activities=_ACTIVITIES) -> None:
        self.users = list(users)
        self.tasks = list(tasks)
        self.activities = list(activities)
        self.queries: list[tuple[str, dict]] = []

    async def fetch(self, sql: str, params: dict) -> list[dict]:
        self.queries.append((sql, params))
        low = sql.lower()
        if "from users" in low:
            return [{"id": u["id"]} for u in self.users if u["nc_user_id"] == params.get("uid")]
        uid = params.get("user_id")
        if "from tasks" in low:
            return [
                dict(t)
                for t in self.tasks
                if (t["owner_id"] == uid or t["assigned_to"] == uid)
                and t.get("deleted_at") is None
            ]
        if "from activities" in low:
            rows = [
                dict(a)
                for a in self.activities
                if (a["owner_id"] == uid or a["assigned_to"] == uid)
                and a.get("deleted_at") is None
            ]
            if params.get("since"):
                rows = [r for r in rows if r["start_date"] and r["start_date"] >= params["since"]]
            if params.get("until"):
                rows = [r for r in rows if r["start_date"] and r["start_date"] <= params["until"]]
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

    Cada query debe bind-ear el parámetro de identidad en su WHERE: `%(uid)s` en la
    resolución (`nc_user_id`) o `%(user_id)s` en las de datos (owner_id/assigned_to).
    """
    assert queries, "no se ejecutó ninguna query"
    for sql, params in queries:
        assert "where" in sql.lower(), f"query sin WHERE: {sql}"
        binds_uid = "%(uid)s" in sql and params.get("uid")
        binds_user_id = "%(user_id)s" in sql and "user_id" in params
        assert binds_uid or binds_user_id, f"query sin filtro de identidad: {sql} / {params}"


@pytest.mark.asyncio
async def test_list_tasks_filters_by_owner_or_assigned_and_excludes_deleted():
    db = FakeDb()

    tasks = await _adapter(db).list_tasks("mmazo")

    # id 1 (owner=7) e id 2 (assigned_to=7); NO id 3 (jdoe) ni id 4 (borrada).
    assert [t.id for t in tasks] == [1, 2]
    assert 3 not in [t.id for t in tasks] and 4 not in [t.id for t in tasks]
    # Mapeo a columnas reales: status ← column_status, due_date ← deadline.
    diseno = next(t for t in tasks if t.id == 1)
    assert diseno.status == "in_progress"
    assert diseno.due_date == "2026-07-10"
    # Resolución primero (uid), luego datos (user_id).
    assert db.queries[0][1] == {"uid": "mmazo"}
    assert db.queries[1][1] == {"user_id": 7}
    _assert_every_query_filters_by_identity(db.queries)


@pytest.mark.asyncio
async def test_list_activities_filters_by_identity_range_and_deleted():
    db = FakeDb()

    activities = await _adapter(db).list_activities(
        "mmazo", since="2026-07-02", until="2026-07-31"
    )

    # id 10 (01-jul) fuera de rango; id 12 de jdoe; id 13 borrada ⇒ solo id 11.
    assert [a.id for a in activities] == [11]
    act = activities[0]
    assert act.time_spent == 3.0          # ← time_spent (no 'hours')
    assert act.date == "2026-07-05"       # ← start_date (no 'log_date')
    assert act.completed is False         # completed_at IS NULL
    assert act.progress == 40
    data_query = db.queries[1]
    assert data_query[1]["user_id"] == 7
    assert data_query[1]["since"] == "2026-07-02" and data_query[1]["until"] == "2026-07-31"
    _assert_every_query_filters_by_identity(db.queries)


@pytest.mark.asyncio
async def test_activities_without_range_still_filters_by_identity():
    db = FakeDb()

    activities = await _adapter(db).list_activities("mmazo")

    # Ambas de mmazo (owner o assigned), no borradas; ninguna de jdoe.
    assert [a.id for a in activities] == [10, 11]
    _assert_every_query_filters_by_identity(db.queries)


@pytest.mark.asyncio
async def test_uid_without_profile_is_refused():
    db = FakeDb()

    with pytest.raises(NoDashboardProfileError, match="perfil"):
        await _adapter(db).list_tasks("desconocido")

    # Solo se intentó resolver identidad; ninguna query de datos.
    assert [q for q in db.queries if "from users" not in q[0].lower()] == []


@pytest.mark.asyncio
async def test_empty_uid_is_rejected():
    db = FakeDb()

    with pytest.raises(DashboardError):
        await _adapter(db).list_tasks("")

    assert db.queries == []


def test_missing_config_rejected_at_construction():
    with pytest.raises(DashboardError):
        DashboardMySQLAdapter(host="", port=3306, name="d", user="u", password="p")
    with pytest.raises(DashboardError):
        DashboardMySQLAdapter(
            host="db-tunnel", port=3306, name="d", user="u", password=""
        )

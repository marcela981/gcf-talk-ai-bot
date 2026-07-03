"""Value objects y parseo del dashboard corporativo (dominio puro, stdlib) — Bloque 3.

El adapter MySQL (infra) hace el I/O contra ``dashboard_db`` y **delega aquí** la
transformación de filas (dicts del cursor) a :class:`DashboardTask` / :class:`DashboardActivity`.
Mismo patrón de capas que ``domain.caldav``/``domain.deck`` (ARCHITECTURE §3): el puerto
``DashboardPort`` habla en estos tipos, no en filas SQL. Solo stdlib ⇒ se testea sin BD.

ESQUEMA (D9): los nombres de columna reflejan el esquema REAL (``SHOW COLUMNS``), NO nombres
adivinados. ``tasks`` usa ``column_status``/``deadline``; ``activities`` no tiene columna de
estado y modela el tiempo con ``time_spent`` + ``completed_at``. La UNIDAD de ``time_spent``
está por confirmar en el smoke (se mapea tal cual a ``time_spent`` y se presenta como "horas").

ALCANCE (ADR-020): SOLO lectura. No hay value objects de escritura.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class DashboardTask:
    """Tarea del dashboard. ``status`` ← ``column_status`` (enum); ``due_date`` ← ``deadline``."""

    id: int
    title: str
    status: str | None = None
    due_date: str | None = None


@dataclass(frozen=True)
class DashboardActivity:
    """Actividad del dashboard (registro de tiempo). ``time_spent`` es su tiempo dedicado.

    ``date`` ← ``start_date``; ``completed`` ← ``completed_at IS NOT NULL``; ``progress`` ←
    ``progress``. ``activities`` no tiene columna de estado equivalente a ``column_status``.
    """

    id: int
    title: str
    time_spent: float
    date: str | None = None
    completed: bool = False
    progress: int | None = None


@dataclass(frozen=True)
class HoursSummary:
    """Resumen de tiempo: total de ``time_spent`` + las actividades que lo componen."""

    since: str | None
    until: str | None
    total: float
    activities: tuple[DashboardActivity, ...]

    @classmethod
    def from_activities(
        cls,
        activities: list[DashboardActivity],
        since: str | None,
        until: str | None,
    ) -> "HoursSummary":
        return cls(
            since=since,
            until=until,
            total=round(sum(a.time_spent for a in activities), 2),
            activities=tuple(activities),
        )


def parse_task(row: dict[str, Any]) -> DashboardTask:
    """Fila de ``tasks`` → :class:`DashboardTask` (columnas reales del esquema)."""
    return DashboardTask(
        id=int(row["id"]),
        title=str(row.get("title") or ""),
        status=(str(row["column_status"]) if row.get("column_status") is not None else None),
        due_date=_as_iso(row.get("deadline")),
    )


def parse_activity(row: dict[str, Any]) -> DashboardActivity:
    """Fila de ``activities`` → :class:`DashboardActivity` (columnas reales del esquema)."""
    return DashboardActivity(
        id=int(row["id"]),
        title=str(row.get("title") or ""),
        time_spent=float(row.get("time_spent") or 0),
        date=_as_iso(row.get("start_date")),
        completed=row.get("completed_at") is not None,
        progress=(int(row["progress"]) if row.get("progress") is not None else None),
    )


def _as_iso(value: Any) -> str | None:
    """Normaliza fechas a ISO: ``date``/``datetime`` → isoformat; ``None`` → ``None``; resto → str."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)

"""Value objects y parseo del dashboard corporativo (dominio puro, stdlib) — Bloque 3.

El adapter MySQL (infra) hace el I/O contra ``dashboard_db`` y **delega aquí** la
transformación de filas (dicts del cursor) a :class:`DashboardTask` / :class:`TimeLog`.
Mismo patrón de capas que ``domain.caldav``/``domain.deck`` (ARCHITECTURE §3): el puerto
``DashboardPort`` habla en estos tipos, no en filas SQL. Solo stdlib ⇒ se testea sin BD.

ALCANCE (ADR-020): SOLO lectura. No hay value objects de escritura — la escritura es un
stub comentado en el adapter y la impide el usuario de BD read-only (ADR-022).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class DashboardTask:
    """Tarea del dashboard asignada al usuario. ``due_date`` es ISO (o ``None``)."""

    id: int
    title: str
    status: str | None = None
    due_date: str | None = None


@dataclass(frozen=True)
class TimeLog:
    """Registro de horas del usuario. ``date`` es ISO (o ``None``); ``hours`` en horas."""

    id: int
    date: str | None
    hours: float
    description: str | None = None


def parse_task(row: dict[str, Any]) -> DashboardTask:
    """Fila de ``tasks`` → :class:`DashboardTask`."""
    return DashboardTask(
        id=int(row["id"]),
        title=str(row.get("title") or ""),
        status=(str(row["status"]) if row.get("status") is not None else None),
        due_date=_as_iso(row.get("due_date")),
    )


def parse_time_log(row: dict[str, Any]) -> TimeLog:
    """Fila de ``time_logs`` → :class:`TimeLog`."""
    return TimeLog(
        id=int(row["id"]),
        date=_as_iso(row.get("log_date")),
        hours=float(row.get("hours") or 0),
        description=(row.get("description") or None),
    )


@dataclass(frozen=True)
class HoursSummary:
    """Resumen de horas del usuario en un rango: total + los registros que lo componen.

    ``since``/``until`` son las cotas ISO consultadas (o ``None`` = sin cota). ``total`` es
    la suma de ``hours`` de ``entries`` (redondeada). Value object de presentación que arma
    la skill a partir de los :class:`TimeLog` que devuelve el port.
    """

    since: str | None
    until: str | None
    total: float
    entries: tuple[TimeLog, ...]

    @classmethod
    def from_logs(
        cls, logs: list[TimeLog], since: str | None, until: str | None
    ) -> "HoursSummary":
        return cls(
            since=since,
            until=until,
            total=total_hours(logs),
            entries=tuple(logs),
        )


def total_hours(logs: list[TimeLog]) -> float:
    """Suma de horas de un conjunto de registros (redondeada a 2 decimales)."""
    return round(sum(log.hours for log in logs), 2)


def _as_iso(value: Any) -> str | None:
    """Normaliza fechas a ISO: ``date``/``datetime`` → isoformat; ``None`` → ``None``; resto → str."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)

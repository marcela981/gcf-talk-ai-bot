"""Skill del dashboard (read-only): `consultar_dashboard` (ADR-020/021/023, Bloque 3).

Expone los datos **estructurados propios del usuario** del dashboard corporativo
(``dashboard_db``): sus **tareas** y sus **horas** registradas. Como las demás skills con
identidad, requiere ``actor.impersonated_uid``; si es ``None`` (invitado/federado) se
**rehúsa** (regla de oro, ADR-021). El I/O vive en el `DashboardPort`; ``execute`` queda
delgado (ADR-018).

FRONTERA DE AUTORIDAD (ADR-023): esta skill cubre lo del **dashboard** (horas/tareas/
histórico/reportes propios), **NO** el estado **en vivo** de Nextcloud. Para el estado
actual de un board de Deck usa `consultar_deck`; para el calendario, `consultar_calendario`.
La ``description`` lo deja explícito para que el LLM enrute bien.

SOLO lectura: no crea ni modifica nada en el dashboard.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.adapters.dashboard_mysql_adapter import NoDashboardProfileError
from app.domain.actor_context import ActorContext
from app.domain.dashboard import DashboardActivity, DashboardTask, HoursSummary
from app.domain.skill_result import SkillResult
from app.services.dashboard_port import DashboardPort

logger = logging.getLogger(__name__)

_NAME = "consultar_dashboard"
_DESCRIPTION = (
    "Consulta el DASHBOARD corporativo (time tracking) del usuario que te escribe: sus "
    "HORAS registradas y sus TAREAS/actividades (reportes e histórico propios). Es LA "
    "fuente para las HORAS, el TIEMPO TRABAJADO, las TAREAS del tracker y el DESEMPEÑO del "
    "dashboard (ADR-023). Úsala SIEMPRE que pregunte por su tiempo, sus registros o su "
    "carga, p. ej.: '¿cuántas horas registré esta semana?', 'cuánto tiempo llevo "
    "trabajado', 'cuánto registré ayer', 'mis horas de julio', 'mi time tracking', "
    "'mis actividades', '¿qué actividades registré?', 'mis tareas del tracker', "
    "'mis tareas del dashboard', '¿qué tareas tengo asignadas?', 'mi carga de trabajo'.\n"
    "Dos recursos: 'horas' (registros de tiempo; acota con 'desde'/'hasta' en ISO "
    "'YYYY-MM-DD', que calculas desde la 'Fecha actual' del contexto) y 'tareas' "
    "(tareas/actividades asignadas del dashboard).\n"
    "IMPORTANTE — consulta la BASE DE DATOS del dashboard corporativo, NO el estado en "
    "vivo de Nextcloud. Para horas/tiempo/actividades/tareas del dashboard usa ESTA tool. "
    "En cambio, para el estado ACTUAL de un tablero de Deck (sus columnas y tarjetas en "
    "vivo) usa 'consultar_deck', y para el calendario 'consultar_calendario'. Devuelve "
    "solo datos del propio usuario. SOLO lectura."
)
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recurso": {
            "type": "string",
            "enum": ["tareas", "horas"],
            "description": "Qué consultar: 'tareas' asignadas o 'horas' registradas.",
        },
        "desde": {
            "type": "string",
            "description": (
                "Inicio del rango en ISO 'YYYY-MM-DD' (solo para 'horas', opcional). "
                "Calcúlalo desde la 'Fecha actual' del contexto."
            ),
        },
        "hasta": {
            "type": "string",
            "description": (
                "Fin del rango en ISO 'YYYY-MM-DD', inclusive (solo para 'horas', "
                "opcional). Debe ser >= 'desde'."
            ),
        },
    },
    "required": ["recurso"],
    "additionalProperties": False,
}

_NO_IDENTITY_MSG = (
    "Acción no disponible para invitados o usuarios sin identidad local: solo "
    "puedo consultar el dashboard de usuarios de Nextcloud."
)


class ConsultarDashboardSkill:
    """Implementa el contrato `Skill` delegando la lectura en un `DashboardPort`."""

    def __init__(self, *, dashboard: DashboardPort) -> None:
        self._dashboard = dashboard

    @property
    def name(self) -> str:
        return _NAME

    @property
    def description(self) -> str:
        return _DESCRIPTION

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return _PARAMETERS_SCHEMA

    async def execute(self, args: dict[str, Any], actor: ActorContext) -> SkillResult:
        """Rehúsa sin identidad; si la hay, consulta tareas u horas del propio usuario."""
        if actor.impersonated_uid is None:
            return SkillResult.failure(_NO_IDENTITY_MSG)

        recurso = str(args.get("recurso") or "").strip().lower()
        if recurso == "tareas":
            return await self._tareas(actor.impersonated_uid)
        if recurso == "horas":
            return await self._horas(args, actor.impersonated_uid)
        return SkillResult.failure("El 'recurso' debe ser 'tareas' u 'horas'.")

    async def _tareas(self, uid: str) -> SkillResult:
        try:
            tasks = await self._dashboard.list_tasks(uid)
        except NoDashboardProfileError as exc:
            return SkillResult.failure(str(exc))
        except Exception as exc:  # noqa: BLE001 — devolver el fallo como dato (ADR-018)
            logger.exception("Consulta de tareas del dashboard falló para %s.", uid)
            return SkillResult.failure(f"Error consultando el dashboard: {exc}")

        return SkillResult.success(
            {
                "recurso": "tareas",
                "total": len(tasks),
                "tareas": [_task_to_dict(t) for t in tasks],
            }
        )

    async def _horas(self, args: dict[str, Any], uid: str) -> SkillResult:
        since, error = _iso_date(args.get("desde"), "desde")
        if error is not None:
            return SkillResult.failure(error)
        until, error = _iso_date(args.get("hasta"), "hasta")
        if error is not None:
            return SkillResult.failure(error)
        if since and until and until < since:
            return SkillResult.failure("El rango es inválido: 'hasta' es anterior a 'desde'.")

        try:
            activities = await self._dashboard.list_activities(
                uid, since=since, until=until
            )
        except NoDashboardProfileError as exc:
            return SkillResult.failure(str(exc))
        except Exception as exc:  # noqa: BLE001 — devolver el fallo como dato (ADR-018)
            logger.exception("Consulta de horas del dashboard falló para %s.", uid)
            return SkillResult.failure(f"Error consultando el dashboard: {exc}")

        summary = HoursSummary.from_activities(activities, since, until)
        return SkillResult.success(
            {
                "recurso": "horas",
                "desde": summary.since,
                "hasta": summary.until,
                "total_horas": summary.total,
                "registros": [_activity_to_dict(a) for a in summary.activities],
            }
        )


def _task_to_dict(task: DashboardTask) -> dict[str, Any]:
    return {
        "titulo": task.title,
        "estado": task.status,
        "vence": task.due_date,
    }


def _activity_to_dict(activity: DashboardActivity) -> dict[str, Any]:
    return {
        "actividad": activity.title,
        "fecha": activity.date,
        "horas": activity.time_spent,
        "completado": activity.completed,
        "progreso": activity.progress,
    }


def _iso_date(raw: Any, field: str) -> tuple[str | None, str | None]:
    """``None``/vacío → ``(None, None)``; ISO válido → ``(iso, None)``; inválido → ``(None, error)``."""
    if raw is None or not str(raw).strip():
        return None, None
    text = str(raw).strip()
    try:
        date.fromisoformat(text)
    except ValueError:
        return None, f"La fecha '{field}' debe ir en formato ISO 'YYYY-MM-DD'."
    return text, None

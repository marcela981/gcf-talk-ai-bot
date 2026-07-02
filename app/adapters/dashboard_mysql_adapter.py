"""Adapter de dashboard_db (MySQL) — implementa `DashboardPort` (SOLO SELECT) — Bloque 3.

Lee ``dashboard_db`` (MySQL en VPS3) para datos estructurados **propios del usuario**
(ADR-020). La conexión llega por el **túnel SSH sidecar** ``db-tunnel`` (Patrón B de
ADR-022): el bot habla a ``db-tunnel:3306`` y el forward emerge en ``127.0.0.1`` de VPS3,
donde el usuario ``gcf_bot_ro`` (``GRANT SELECT``) atiende. Triple candado read-only:
usuario de BD sin escritura (ADR-022) + adapter que solo hace ``SELECT`` + escritura como
stub comentado.

REGLA DE ORO — IDENTIDAD (ADR-021): cada método recibe el ``uid`` (no opcional), resuelve
``SELECT id FROM users WHERE nc_user_id = :uid`` y filtra **TODA** query por ese
``users.id`` (``owner_id``/``user_id``/``assigned_to``). **No hay** método de query libre.
``uid`` sin fila ⇒ :class:`NoDashboardProfileError`. El ``password`` NUNCA se loguea; el DSN
no se construye con el secreto en claro en logs.

El driver MySQL (``asyncmy``) se importa **de forma perezosa** (solo si se usa el fetch
real), igual que el RAG. ``fetch`` es inyectable para tests sin BD.

NOTA (D9): los nombres de tabla/columna (``users.nc_user_id``/``users.id``, ``tasks``,
``time_logs``) pertenecen al esquema del dashboard (otro repo/equipo, ADR-020) — **este
adapter es la ÚNICA capa que los conoce**. Confirmar contra el esquema real; los cambios se
absorben aquí (o vía vistas SQL estables).
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.domain.dashboard import DashboardTask, TimeLog, parse_task, parse_time_log

logger = logging.getLogger(__name__)

# Fetch inyectable: (sql, params) -> filas como dicts. Aísla el driver del test.
FetchFn = Callable[[str, dict[str, Any]], Awaitable[list[dict[str, Any]]]]

# --- SQL (D9: esquema propiedad del dashboard; identidad SIEMPRE en el WHERE) ---
_SQL_RESOLVE_USER = "SELECT id FROM users WHERE nc_user_id = %(uid)s LIMIT 1"
_SQL_TASKS = (
    "SELECT id, title, status, due_date FROM tasks "
    "WHERE assigned_to = %(user_id)s "
    "ORDER BY due_date IS NULL, due_date, id"
)


class DashboardError(Exception):
    """Fallo del adapter del dashboard (config, conexión, consulta)."""


class NoDashboardProfileError(DashboardError):
    """El ``uid`` de Nextcloud no tiene fila en ``users`` (sin perfil en el dashboard)."""


class DashboardMySQLAdapter:
    """Implementa `DashboardPort` contra ``dashboard_db`` (MySQL), SOLO SELECT."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        name: str,
        user: str,
        password: str,
        ssl: Any | None = None,
        fetch: FetchFn | None = None,
    ) -> None:
        if not host or not name or not user or not password:
            raise DashboardError(
                "DASHBOARD_DB_HOST/NAME/USER/PASSWORD son obligatorios para el "
                "adapter del dashboard."
            )
        self._host = host
        self._port = port
        self._name = name
        self._user = user
        self._password = password  # NUNCA se loguea
        self._ssl = ssl
        self._fetch_impl = fetch  # None ⇒ driver real (asyncmy) perezoso

    async def list_tasks(self, uid: str) -> list[DashboardTask]:
        user_id = await self._resolve_user_id(uid)
        rows = await self._fetch(_SQL_TASKS, {"user_id": user_id})
        return [parse_task(row) for row in rows]

    async def list_time_logs(
        self, uid: str, *, since: str | None = None, until: str | None = None
    ) -> list[TimeLog]:
        user_id = await self._resolve_user_id(uid)
        sql, params = _time_logs_query(user_id, since, until)
        rows = await self._fetch(sql, params)
        return [parse_time_log(row) for row in rows]

    # --- ESCRITURA: FUERA DE ALCANCE (ADR-020). Stubs comentados a propósito;
    #     el usuario de BD read-only (gcf_bot_ro, GRANT SELECT) la impide igual.
    #     Habilitarla exigiría otro ADR + otro usuario/gate de credenciales.
    #
    # async def create_task(self, uid: str, ...) -> ...:
    #     raise NotImplementedError("Escritura fuera de alcance (ADR-020/022).")
    #
    # async def log_time(self, uid: str, ...) -> ...:
    #     raise NotImplementedError("Escritura fuera de alcance (ADR-020/022).")

    # --- identidad + fetch ---------------------------------------------------

    async def _resolve_user_id(self, uid: str) -> int:
        """``nc_user_id`` → ``users.id`` (regla de oro, ADR-021). Sin fila ⇒ rehúse claro."""
        if not uid:
            raise DashboardError("uid vacío: no hay identidad que resolver.")
        rows = await self._fetch(_SQL_RESOLVE_USER, {"uid": uid})
        if not rows:
            raise NoDashboardProfileError(
                f"El usuario {uid!r} no tiene perfil en el dashboard corporativo."
            )
        return int(rows[0]["id"])

    async def _fetch(
        self, sql: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        impl = self._fetch_impl or self._default_fetch
        return await impl(sql, params)

    async def _default_fetch(
        self, sql: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Ejecuta el SELECT con ``asyncmy`` (import perezoso), cursor de dicts, read-only."""
        asyncmy, dict_cursor = _import_asyncmy()
        conn = await asyncmy.connect(
            host=self._host,
            port=self._port,
            database=self._name,
            user=self._user,
            password=self._password,
            ssl=self._ssl,
        )
        try:
            async with conn.cursor(cursor=dict_cursor) as cur:
                await cur.execute(sql, params)
                return list(await cur.fetchall())
        finally:
            await conn.ensure_closed()  # asyncmy: el cierre es una corrutina


def _time_logs_query(
    user_id: int, since: str | None, until: str | None
) -> tuple[str, dict[str, Any]]:
    """Query de ``time_logs`` SIEMPRE filtrada por identidad; fecha = filtro ADICIONAL."""
    sql = "SELECT id, log_date, hours, description FROM time_logs WHERE user_id = %(user_id)s"
    params: dict[str, Any] = {"user_id": user_id}
    if since:
        sql += " AND log_date >= %(since)s"
        params["since"] = since
    if until:
        sql += " AND log_date <= %(until)s"
        params["until"] = until
    sql += " ORDER BY log_date, id"
    return sql, params


def _import_asyncmy():
    """Import perezoso del driver MySQL async (``asyncmy`` + su ``DictCursor``).

    Ausencia ⇒ error claro del adapter (solo se necesita si ``dashboard_ready``).
    """
    try:
        import asyncmy
        from asyncmy.cursors import DictCursor
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise DashboardError(
            "Falta el driver 'asyncmy' para conectar a dashboard_db; instala "
            "requirements.txt (solo se necesita si dashboard_ready)."
        ) from exc
    return asyncmy, DictCursor

"""Contrato `DashboardPort` (ADR-020/021, Bloque 3): dashboard_db impersonado (SOLO lectura).

Puerto de **recuperación estructurada**, hermano del RAG (ADR-020): aporta CONTEXTO
(tareas/horas/desempeño propios del usuario), NO acciones, y **no sustituye** a las skills
en vivo de Nextcloud (ADR-023). Las skills dependen de esta interfaz, no del driver MySQL;
el adapter (``adapters/dashboard_mysql_adapter.py``) la implementa. Sin dependencias de
framework (ARCHITECTURE §3): el contrato vive en ``services`` y habla en value objects
(``app.domain.dashboard``).

REGLA DE ORO — IDENTIDAD (ADR-021): el ``uid`` del usuario es un **parámetro estructural NO
opcional** de cada método. El adapter lo resuelve (``nc_user_id`` → ``users.id`` interno) y
filtra **TODA** query por ese id. **NO existe** ningún método de "query libre" ni ninguna
consulta sin filtro de identidad. Guests/sin ``uid`` ⇒ la skill rehúsa (como las skills en
vivo). ``uid`` sin fila en ``users`` ⇒ error de dominio "sin perfil en el dashboard".

ESCRITURA: fuera de alcance (ADR-020). No se declara aquí; en el adapter queda como stub
comentado y el usuario de BD read-only la impide (ADR-022).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.dashboard import DashboardTask, TimeLog


@runtime_checkable
class DashboardPort(Protocol):
    """Acceso de solo-lectura a los datos del dashboard de un usuario, bajo SU identidad."""

    async def list_tasks(self, uid: str) -> list[DashboardTask]:
        """Tareas asignadas al usuario ``uid`` en el dashboard, filtradas por su identidad.

        Resuelve ``uid`` → ``users.id`` y filtra ``tasks`` por ese id. Lanza un error del
        adapter si ``uid`` no tiene perfil en el dashboard o ante fallo de conexión/consulta
        (la skill lo traduce a ``SkillResult.failure``). **Nunca** devuelve datos de terceros.
        """
        ...

    async def list_time_logs(
        self, uid: str, *, since: str | None = None, until: str | None = None
    ) -> list[TimeLog]:
        """Registros de horas del usuario ``uid``, opcionalmente acotados por rango de fechas.

        ``since``/``until`` son fechas ISO ``YYYY-MM-DD`` inclusivas (o ``None`` = sin cota).
        Siempre filtra por la identidad resuelta (``user_id = users.id``); el rango de fechas
        es un filtro **adicional**, nunca sustituye al de identidad. Mismo contrato de errores
        que :meth:`list_tasks`.
        """
        ...

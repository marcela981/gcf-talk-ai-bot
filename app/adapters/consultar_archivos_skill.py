"""Skill de Files (read-only): `consultar_archivos` (ADR-016/ADR-018, Bloque 2.4).

Como las demás skills de Nextcloud, **usa la identidad**: requiere ``actor.impersonated_uid``;
si es ``None`` (invitado/federado, ADR-016) se **rehúsa**, SIN tocar Files. El I/O vive en el
`FilesPort` inyectado; ``execute`` queda delgado (ADR-018).

Acciones:
  * ``listar`` — hijos directos de ``carpeta`` (default raíz). Con ``nombre`` filtra por
    subcadena del nombre (búsqueda **simple, no recursiva** — KISS; sin REPORT SEARCH).
  * ``leer``   — contenido de texto de ``ruta`` (rechaza binarios/grandes en el adapter).

SOLO lectura: no sube, mueve ni borra (la escritura es el Bloque 2.4b).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.domain.actor_context import ActorContext
from app.domain.files import FileEntry
from app.domain.skill_result import SkillResult
from app.services.files_port import FilesPort

logger = logging.getLogger(__name__)

_NAME = "consultar_archivos"
_DESCRIPTION = (
    "Consulta los archivos de Nextcloud del usuario que te escribe. Úsala cuando pregunte "
    "por sus archivos o carpetas, quiera buscar un archivo o leer su contenido, p. ej.: "
    "'¿qué archivos tengo en Documentos?', 'lista mi carpeta Proyectos', 'búscame el "
    "archivo notas.txt', 'léeme el contenido de Documentos/informe.md'. Dos acciones: "
    "'listar' (con 'carpeta'; añade 'nombre' para filtrar por nombre — búsqueda simple, no "
    "recursiva) y 'leer' (con 'ruta' del archivo, devuelve solo texto; los binarios o "
    "archivos muy grandes se rechazan). Las rutas son relativas a la raíz del usuario, con "
    "'/' inicial (p. ej. '/Documentos/informe.md'). SOLO lectura: no crea, mueve ni borra."
)
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "accion": {
            "type": "string",
            "enum": ["listar", "leer"],
            "description": "'listar' una carpeta o 'leer' un archivo de texto.",
        },
        "carpeta": {
            "type": "string",
            "description": (
                "Carpeta a listar (para 'listar'), relativa al usuario con '/' inicial "
                "(p. ej. '/Documentos'). Omítela para la raíz '/'."
            ),
        },
        "nombre": {
            "type": "string",
            "description": (
                "Para 'listar': filtra las entradas cuyo nombre contenga este texto "
                "(case-insensitive). Búsqueda simple dentro de 'carpeta', no recursiva."
            ),
        },
        "ruta": {
            "type": "string",
            "description": (
                "Para 'leer': ruta del archivo relativa al usuario con '/' inicial "
                "(p. ej. '/Documentos/informe.md')."
            ),
        },
    },
    "required": ["accion"],
    "additionalProperties": False,
}

_NO_IDENTITY_MSG = (
    "Acción no disponible para invitados o usuarios sin identidad local: solo "
    "puedo consultar los archivos de usuarios de Nextcloud."
)


class ConsultarArchivosSkill:
    """Implementa el contrato `Skill` delegando la lectura en un `FilesPort`."""

    def __init__(self, *, files: FilesPort) -> None:
        self._files = files

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
        """Rehúsa sin identidad; si la hay, lista una carpeta o lee un archivo via `FilesPort`."""
        if actor.impersonated_uid is None:
            return SkillResult.failure(_NO_IDENTITY_MSG)

        accion = str(args.get("accion") or "").strip().lower()
        if accion == "listar":
            return await self._listar(args, actor.impersonated_uid)
        if accion == "leer":
            return await self._leer(args, actor.impersonated_uid)
        return SkillResult.failure("La 'accion' debe ser 'listar' o 'leer'.")

    async def _listar(self, args: dict[str, Any], uid: str) -> SkillResult:
        carpeta = str(args.get("carpeta") or "/").strip() or "/"
        try:
            entries = await self._files.list_files(uid, carpeta)
        except Exception as exc:  # noqa: BLE001 — devolver el fallo como dato (ADR-018)
            logger.exception("Listado de archivos falló para %s en %r.", uid, carpeta)
            return SkillResult.failure(f"Error listando archivos: {exc}")

        filtro = str(args.get("nombre") or "").strip()
        if filtro:
            needle = filtro.casefold()
            entries = [e for e in entries if needle in e.name.casefold()]

        return SkillResult.success(
            {
                "carpeta": carpeta,
                "filtro": filtro or None,
                "total": len(entries),
                "entradas": [_entry_to_dict(e) for e in entries],
            }
        )

    async def _leer(self, args: dict[str, Any], uid: str) -> SkillResult:
        ruta = str(args.get("ruta") or "").strip()
        if not ruta:
            return SkillResult.failure("Para 'leer' indica la 'ruta' del archivo.")
        try:
            contenido = await self._files.read_text_file(uid, ruta)
        except Exception as exc:  # noqa: BLE001 — devolver el fallo como dato (ADR-018)
            logger.exception("Lectura de archivo falló para %s en %r.", uid, ruta)
            return SkillResult.failure(f"Error leyendo el archivo: {exc}")

        return SkillResult.success(
            {
                "ruta": ruta,
                "caracteres": len(contenido),
                "contenido": contenido,
            }
        )


def _entry_to_dict(entry: FileEntry) -> dict[str, Any]:
    return {
        "nombre": entry.name,
        "ruta": entry.path,
        "tipo": "carpeta" if entry.is_dir else "archivo",
        "tamano_bytes": entry.size,
        "modificado": _iso(entry.modified),
        "mime": entry.mime,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None

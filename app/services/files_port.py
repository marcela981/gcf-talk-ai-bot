"""Contrato `FilesPort` (ADR-016/ADR-018, Bloque 2.4): Files impersonado (SOLO lectura).

Mismo patrón que `CalendarPort`/`DeckPort`: las skills dependen de esta interfaz, no del
adapter WebDAV concreto; el adapter de Nextcloud (``adapters/nextcloud_files_adapter.py``)
la implementa. Sin dependencias de framework (regla de capas, ARCHITECTURE §3): el contrato
vive en ``services`` y habla en value objects de dominio (``app.domain.files``).

Puerto **SOLO LECTURA** (2.4): listar y leer texto. La **escritura** (subir/mover/borrar)
queda para el Bloque **2.4b** y comparte el gate de validación de escritura impersonada
(Track A). La búsqueda por nombre se resuelve como filtro cliente sobre :meth:`list_files`
(KISS): no se implementa el REPORT ``SEARCH`` de WebDAV todavía.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.files import FileEntry


@runtime_checkable
class FilesPort(Protocol):
    """Acceso de solo-lectura a los archivos de un usuario, bajo SU identidad."""

    async def list_files(self, uid: str, path: str = "/") -> list[FileEntry]:
        """Entradas (nombre, ruta, tipo, tamaño, mtime) de la carpeta ``path`` de ``uid``.

        PROPFIND Depth 1: lista los hijos directos de ``path`` (no recursivo), excluyendo
        la carpeta misma. Actúa **impersonando** a ``uid`` (ADR-016). Puede lanzar un error
        propio del adapter ante fallo de transporte/HTTP (403/404 → error claro); el
        llamador (la skill) lo traduce a ``SkillResult.failure`` (ADR-017/018).
        """
        ...

    async def read_text_file(self, uid: str, path: str) -> str:
        """Contenido de **texto** del archivo ``path`` de ``uid``, **impersonando** a ``uid``.

        Rechaza binarios y archivos grandes: si excede el límite de tamaño configurado o
        el contenido no es texto (UTF-8), **lanza** un error del adapter con mensaje claro
        (la skill lo convierte en ``SkillResult.failure``). 403/404 también se reportan como
        error claro. Escritura NO soportada aquí (2.4b).
        """
        ...

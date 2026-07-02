"""Unit tests para ConsultarArchivosSkill (lectura de Files, ADR-016/018, Bloque 2.4).

El `FilesPort` se reemplaza por un `FakeFiles` — sin red. Se verifica que la skill: se
REHÚSA sin identidad (uid None), lista una carpeta, filtra por 'nombre' (búsqueda simple),
lee el contenido de un archivo, valida la acción y la 'ruta' de lectura, y convierte los
fallos del port (403/binario/…) en `SkillResult.failure` (dato, no excepción).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.adapters.consultar_archivos_skill import ConsultarArchivosSkill
from app.adapters.nextcloud_files_adapter import FilesError
from app.domain.actor_context import ActorContext
from app.domain.files import FileEntry

_USER = ActorContext(actor_id="users/mmazo", token="room1", impersonated_uid="mmazo")
_GUEST = ActorContext(actor_id="guests/abc", token="room1", impersonated_uid=None)

_ENTRIES = [
    FileEntry(name="Documentos", path="/Documentos", is_dir=True, size=2048),
    FileEntry(
        name="notas.txt",
        path="/notas.txt",
        is_dir=False,
        size=42,
        modified=datetime(2026, 6, 24, 9, 15, tzinfo=timezone.utc),
        mime="text/plain",
    ),
    FileEntry(name="informe.md", path="/informe.md", is_dir=False, size=99, mime="text/markdown"),
]


class FakeFiles:
    def __init__(self, entries=None, text=None) -> None:
        self._entries = entries or []
        self._text = text
        self.calls: list[tuple] = []

    async def list_files(self, uid, path="/"):
        self.calls.append(("list_files", uid, path))
        return list(self._entries)

    async def read_text_file(self, uid, path):
        self.calls.append(("read_text_file", uid, path))
        return self._text


class RaisingFiles:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def list_files(self, uid, path="/"):
        raise self._error

    async def read_text_file(self, uid, path):
        raise self._error


@pytest.mark.asyncio
async def test_refuses_without_local_identity():
    files = FakeFiles(entries=_ENTRIES)
    skill = ConsultarArchivosSkill(files=files)

    result = await skill.execute({"accion": "listar", "carpeta": "/Documentos"}, _GUEST)

    assert not result.ok
    assert "invitados" in result.error
    assert files.calls == []


@pytest.mark.asyncio
async def test_listar_folder_returns_entries():
    files = FakeFiles(entries=_ENTRIES)
    skill = ConsultarArchivosSkill(files=files)

    result = await skill.execute({"accion": "listar", "carpeta": "/Documentos"}, _USER)

    assert result.ok
    assert result.data["carpeta"] == "/Documentos"
    assert result.data["total"] == 3
    assert result.data["entradas"][0] == {
        "nombre": "Documentos",
        "ruta": "/Documentos",
        "tipo": "carpeta",
        "tamano_bytes": 2048,
        "modificado": None,
        "mime": None,
    }
    assert result.data["entradas"][1]["tipo"] == "archivo"
    assert result.data["entradas"][1]["modificado"] == "2026-06-24T09:15:00+00:00"
    assert files.calls == [("list_files", "mmazo", "/Documentos")]


@pytest.mark.asyncio
async def test_listar_defaults_to_root():
    files = FakeFiles(entries=_ENTRIES)
    skill = ConsultarArchivosSkill(files=files)

    result = await skill.execute({"accion": "listar"}, _USER)

    assert result.ok
    assert result.data["carpeta"] == "/"
    assert files.calls == [("list_files", "mmazo", "/")]


@pytest.mark.asyncio
async def test_listar_with_nombre_filters_by_substring():
    files = FakeFiles(entries=_ENTRIES)
    skill = ConsultarArchivosSkill(files=files)

    result = await skill.execute({"accion": "listar", "nombre": "NOTAS"}, _USER)

    assert result.ok
    assert result.data["filtro"] == "NOTAS"
    assert [e["nombre"] for e in result.data["entradas"]] == ["notas.txt"]
    assert result.data["total"] == 1


@pytest.mark.asyncio
async def test_leer_returns_content():
    files = FakeFiles(text="hola mundo")
    skill = ConsultarArchivosSkill(files=files)

    result = await skill.execute({"accion": "leer", "ruta": "/Documentos/nota.txt"}, _USER)

    assert result.ok
    assert result.data["ruta"] == "/Documentos/nota.txt"
    assert result.data["contenido"] == "hola mundo"
    assert result.data["caracteres"] == 10
    assert files.calls == [("read_text_file", "mmazo", "/Documentos/nota.txt")]


@pytest.mark.asyncio
async def test_leer_without_ruta_is_failure():
    files = FakeFiles()
    skill = ConsultarArchivosSkill(files=files)

    result = await skill.execute({"accion": "leer"}, _USER)

    assert not result.ok
    assert "ruta" in result.error
    assert files.calls == []


@pytest.mark.asyncio
async def test_invalid_accion_is_failure():
    files = FakeFiles()
    skill = ConsultarArchivosSkill(files=files)

    result = await skill.execute({"accion": "borrar", "ruta": "/x"}, _USER)

    assert not result.ok
    assert "listar" in result.error and "leer" in result.error
    assert files.calls == []


@pytest.mark.asyncio
async def test_list_error_becomes_failure():
    skill = ConsultarArchivosSkill(files=RaisingFiles(FilesError("HTTP 404")))

    result = await skill.execute({"accion": "listar", "carpeta": "/NoExiste"}, _USER)

    assert not result.ok
    assert "listando" in result.error.lower()


@pytest.mark.asyncio
async def test_read_error_becomes_failure():
    skill = ConsultarArchivosSkill(
        files=RaisingFiles(FilesError("El archivo no es de texto (parece binario)."))
    )

    result = await skill.execute({"accion": "leer", "ruta": "/img.png"}, _USER)

    assert not result.ok
    assert "leyendo" in result.error.lower()


def test_tool_schema_is_public_contract():
    skill = ConsultarArchivosSkill(files=FakeFiles())

    assert skill.name == "consultar_archivos"
    schema = skill.parameters_schema
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["accion"]
    assert schema["properties"]["accion"]["enum"] == ["listar", "leer"]
    for key in ("accion", "carpeta", "nombre", "ruta"):
        assert key in schema["properties"]

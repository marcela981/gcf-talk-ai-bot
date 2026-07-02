"""Unit tests para DeckRestAdapter (Bloque 2.3), sin red.

Se inyecta un `httpx.MockTransport` (mismo patrón que los tests de Calendar) para ejercer
el adapter sin tocar Nextcloud. Se verifica: list_boards parsea la muestra del spike
(ids 89, 71); get_board_status resuelve el tablero por título y arma stacks+cards; el
create_card resuelve board/columna por título y hace POST a la RUTA correcta
(`/boards/{id}/stacks/{id}/cards`) con el header de impersonation
`AUTHORIZATION-APP-API = b64(uid:app_secret)` (secreto NUNCA en claro), `OCS-APIRequest`
y `Content-Type: application/json`; 201 ⇒ éxito con id; 403 ⇒ error como dato; tablero o
columna inexistente ⇒ error claro; y el rechazo con uid vacío.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.adapters.nextcloud_deck_adapter import DeckError, DeckRestAdapter

_SECRET = "s3cr3t-app-secret"
_API = "/index.php/apps/deck/api/v1.0"

# Muestra del spike: GET /boards devolvió 2 boards (89, 71).
_BOARDS = [
    {"id": 89, "title": "TECH PROY"},
    {"id": 71, "title": "Ventas"},
]
_STACKS_89 = [
    {
        "id": 5,
        "title": "To Do",
        "cards": [
            {
                "id": 100,
                "title": "Diseñar API",
                "description": "un detalle",
                "duedate": "2026-07-15T17:00:00+00:00",
            },
            {"id": 101, "title": "Revisar PR", "description": None, "duedate": None},
        ],
    },
    {"id": 6, "title": "Doing", "cards": []},
]


def _adapter(handler) -> DeckRestAdapter:
    return DeckRestAdapter(
        endpoint="https://nc.example.com",
        app_id="gcf_bot",
        app_version="1.2.3",
        app_secret=_SECRET,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_list_boards_parses_spike_sample():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"{_API}/boards"
        assert request.headers["OCS-APIRequest"] == "true"
        return httpx.Response(200, json=_BOARDS)

    boards = await _adapter(handler).list_boards("mmazo")

    assert [b.id for b in boards] == [89, 71]
    assert [b.title for b in boards] == ["TECH PROY", "Ventas"]


@pytest.mark.asyncio
async def test_get_board_status_by_title_builds_stacks_and_cards():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == f"{_API}/boards":
            return httpx.Response(200, json=_BOARDS)
        if request.url.path == f"{_API}/boards/89/stacks":
            return httpx.Response(200, json=_STACKS_89)
        return httpx.Response(404)

    # Se resuelve por título case-insensitive ("tech proy" -> board 89).
    status = await _adapter(handler).get_board_status("mmazo", "tech proy")

    assert status.board.id == 89
    assert status.board.title == "TECH PROY"
    assert [s.title for s in status.stacks] == ["To Do", "Doing"]
    todo = status.stacks[0]
    assert [c.title for c in todo.cards] == ["Diseñar API", "Revisar PR"]
    assert todo.cards[0].due_date == "2026-07-15T17:00:00+00:00"
    assert todo.cards[1].description is None
    # Rutas: primero /boards, luego /boards/89/stacks.
    assert seen[0].url.path == f"{_API}/boards"
    assert seen[1].url.path == f"{_API}/boards/89/stacks"


@pytest.mark.asyncio
async def test_get_board_status_unknown_board_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_BOARDS)

    with pytest.raises(DeckError, match="tablero"):
        await _adapter(handler).get_board_status("mmazo", "NoExiste")


@pytest.mark.asyncio
async def test_create_card_resolves_titles_and_posts_to_correct_route():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET" and request.url.path == f"{_API}/boards":
            return httpx.Response(200, json=_BOARDS)
        if request.method == "GET" and request.url.path == f"{_API}/boards/89/stacks":
            return httpx.Response(200, json=_STACKS_89)
        if request.method == "POST":
            return httpx.Response(201, json={"id": 999, "title": "Nueva"})
        return httpx.Response(405)

    duedate = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    result = await _adapter(handler).create_card(
        "mmazo", "TECH PROY", "to do", "Nueva", description="con detalle", duedate=duedate
    )

    assert result.ok is True
    assert result.status == 201
    assert result.card_id == 999

    post = next(r for r in seen if r.method == "POST")
    # RUTA: /boards/89/stacks/5/cards (board y stack resueltos por título).
    assert post.url.path == f"{_API}/boards/89/stacks/5/cards"
    assert post.headers["Content-Type"] == "application/json"
    assert post.headers["OCS-APIRequest"] == "true"

    body = json.loads(post.content)
    assert body["title"] == "Nueva"
    assert body["type"] == "plain"
    assert body["order"] == 2  # se agrega al final de "To Do" (2 tarjetas existentes)
    assert body["description"] == "con detalle"
    assert body["duedate"] == "2026-07-15T12:00:00+00:00"

    # Impersonation: uid embebido en AUTHORIZATION-APP-API; secreto NUNCA en claro.
    token = post.headers["AUTHORIZATION-APP-API"]
    assert base64.b64decode(token).decode("utf-8") == f"mmazo:{_SECRET}"
    assert _SECRET not in post.content.decode("utf-8")
    for request in seen:
        for key, value in request.headers.items():
            if key.lower() != "authorization-app-api":
                assert _SECRET not in value


@pytest.mark.asyncio
async def test_create_card_403_returns_error_result_not_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == f"{_API}/boards":
            return httpx.Response(200, json=_BOARDS)
        if request.method == "GET":
            return httpx.Response(200, json=_STACKS_89)
        return httpx.Response(403, text="forbidden")

    result = await _adapter(handler).create_card("mmazo", "TECH PROY", "To Do", "X")

    assert result.ok is False
    assert result.status == 403
    assert result.card_id is None
    assert "403" in result.error


@pytest.mark.asyncio
async def test_create_card_unknown_board_returns_error_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_BOARDS)

    result = await _adapter(handler).create_card("mmazo", "Fantasma", "To Do", "X")

    assert result.ok is False
    assert "tablero" in result.error.lower()


@pytest.mark.asyncio
async def test_create_card_unknown_stack_returns_error_result():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"{_API}/boards":
            return httpx.Response(200, json=_BOARDS)
        return httpx.Response(200, json=_STACKS_89)

    result = await _adapter(handler).create_card("mmazo", "TECH PROY", "Backlog", "X")

    assert result.ok is False
    assert "columna" in result.error.lower()


@pytest.mark.asyncio
async def test_empty_uid_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_BOARDS)

    adapter = _adapter(handler)
    with pytest.raises(DeckError):
        await adapter.list_boards("")
    with pytest.raises(DeckError):
        await adapter.create_card("", "TECH PROY", "To Do", "X")

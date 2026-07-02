"""Unit tests para ConsultarDeckSkill (lectura de Deck, ADR-016/018, Bloque 2.3).

El `DeckPort` se reemplaza por un `FakeDeck` — sin red. Se verifica que la skill: se REHÚSA
sin identidad (uid None), lista tableros cuando no se da 'tablero', arma columnas+tarjetas
para un tablero, filtra por 'columna' (case-insensitive), recorta la descripción larga, y
convierte un fallo del port (tablero inexistente) en `SkillResult.failure` (dato).
"""
from __future__ import annotations

import pytest

from app.adapters.consultar_deck_skill import ConsultarDeckSkill
from app.adapters.nextcloud_deck_adapter import DeckError
from app.domain.actor_context import ActorContext
from app.domain.deck import Board, BoardStatus, Card, Stack

_USER = ActorContext(actor_id="users/mmazo", token="room1", impersonated_uid="mmazo")
_GUEST = ActorContext(actor_id="guests/abc", token="room1", impersonated_uid=None)


class FakeDeck:
    def __init__(self, boards=None, status=None) -> None:
        self._boards = boards or []
        self._status = status
        self.calls: list[tuple] = []

    async def list_boards(self, uid):
        self.calls.append(("list_boards", uid))
        return list(self._boards)

    async def get_board_status(self, uid, board):
        self.calls.append(("get_board_status", uid, board))
        return self._status


class RaisingDeck:
    async def list_boards(self, uid):
        raise DeckError("Deck 500")

    async def get_board_status(self, uid, board):
        raise DeckError(f"No encontré un tablero que coincida con {board!r}.")


@pytest.mark.asyncio
async def test_refuses_without_local_identity():
    deck = FakeDeck()
    skill = ConsultarDeckSkill(deck=deck)

    result = await skill.execute({"tablero": "TECH PROY"}, _GUEST)

    assert not result.ok
    assert "invitados" in result.error
    assert deck.calls == []


@pytest.mark.asyncio
async def test_lists_boards_when_no_board_given():
    deck = FakeDeck(boards=[Board(89, "TECH PROY"), Board(71, "Ventas")])
    skill = ConsultarDeckSkill(deck=deck)

    result = await skill.execute({}, _USER)

    assert result.ok
    assert result.data["total"] == 2
    assert result.data["tableros"] == [
        {"id": 89, "titulo": "TECH PROY"},
        {"id": 71, "titulo": "Ventas"},
    ]
    assert deck.calls == [("list_boards", "mmazo")]


@pytest.mark.asyncio
async def test_board_status_with_columna_filter():
    status = BoardStatus(
        board=Board(89, "TECH PROY"),
        stacks=(
            Stack(5, "To Do", (Card(100, "Diseñar API", "detalle", None),)),
            Stack(6, "Doing", (Card(101, "Otra", None, None),)),
        ),
    )
    deck = FakeDeck(status=status)
    skill = ConsultarDeckSkill(deck=deck)

    result = await skill.execute({"tablero": "89", "columna": "to do"}, _USER)

    assert result.ok
    assert result.data["tablero"] == {"id": 89, "titulo": "TECH PROY"}
    # Solo la columna filtrada (case-insensitive).
    assert [c["columna"] for c in result.data["columnas"]] == ["To Do"]
    assert result.data["total_tarjetas"] == 1
    assert result.data["columnas"][0]["tarjetas"][0]["titulo"] == "Diseñar API"
    assert deck.calls == [("get_board_status", "mmazo", "89")]


@pytest.mark.asyncio
async def test_long_description_is_trimmed():
    long_desc = "x" * 500
    status = BoardStatus(
        board=Board(89, "B"),
        stacks=(Stack(5, "To Do", (Card(100, "T", long_desc, None),)),),
    )
    skill = ConsultarDeckSkill(deck=FakeDeck(status=status))

    result = await skill.execute({"tablero": "89"}, _USER)

    presented = result.data["columnas"][0]["tarjetas"][0]["descripcion"]
    assert presented.endswith("…")
    assert len(presented) <= 201  # 200 + la elipsis


@pytest.mark.asyncio
async def test_unknown_board_becomes_failure():
    skill = ConsultarDeckSkill(deck=RaisingDeck())

    result = await skill.execute({"tablero": "NoExiste"}, _USER)

    assert not result.ok
    assert "deck" in result.error.lower()


def test_tool_schema_is_public_contract():
    skill = ConsultarDeckSkill(deck=FakeDeck())

    assert skill.name == "consultar_deck"
    schema = skill.parameters_schema
    assert schema["additionalProperties"] is False
    assert "tablero" in schema["properties"]
    assert "columna" in schema["properties"]

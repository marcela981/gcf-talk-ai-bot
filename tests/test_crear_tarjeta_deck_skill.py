"""Unit tests para CrearTarjetaDeckSkill (escritura de Deck, ADR-016/018, Bloque 2.3).

El `DeckPort` se reemplaza por un `FakeDeck` — sin red. Se verifica que la skill: se REHÚSA
sin identidad (uid None), valida titulo/board/columna, ancla 'fecha_limite' a mediodía en
la zona del usuario, delega en el port, y convierte tanto un `CreatedCard` de error
(p. ej. 403) como una excepción en `SkillResult.failure` (dato, no excepción).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.adapters.crear_tarjeta_deck_skill import CrearTarjetaDeckSkill
from app.domain.actor_context import ActorContext
from app.domain.deck import CreatedCard

BOGOTA = ZoneInfo("America/Bogota")  # UTC-5, sin DST

_USER = ActorContext(actor_id="users/mmazo", token="room1", impersonated_uid="mmazo")
_GUEST = ActorContext(actor_id="guests/abc", token="room1", impersonated_uid=None)


class FakeDeck:
    def __init__(self, created: CreatedCard | None = None) -> None:
        self._created = created or CreatedCard(
            ok=True, status=201, card_id=999, url="https://nc/deck/999"
        )
        self.calls: list[dict] = []

    async def create_card(
        self, uid, board, stack, title, *, description=None, duedate=None
    ) -> CreatedCard:
        self.calls.append(
            {
                "uid": uid,
                "board": board,
                "stack": stack,
                "title": title,
                "description": description,
                "duedate": duedate,
            }
        )
        return self._created


class BoomDeck:
    async def create_card(self, *args, **kwargs) -> CreatedCard:
        raise RuntimeError("Deck caído")


def _args(**overrides) -> dict:
    base = {"titulo": "Llamar cliente", "board": "TECH PROY", "columna": "To Do"}
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_refuses_without_local_identity():
    deck = FakeDeck()
    skill = CrearTarjetaDeckSkill(deck=deck, tz=BOGOTA)

    result = await skill.execute(_args(), _GUEST)

    assert not result.ok
    assert "invitados" in result.error
    assert deck.calls == []


@pytest.mark.asyncio
async def test_creates_card_delegating_with_duedate_at_noon_local():
    deck = FakeDeck()
    skill = CrearTarjetaDeckSkill(deck=deck, tz=BOGOTA)

    result = await skill.execute(
        _args(descripcion="con notas", fecha_limite="2026-07-15"), _USER
    )

    assert result.ok
    assert result.data["creada"] is True
    assert result.data["id"] == 999
    assert result.data["tablero"] == "TECH PROY"
    assert result.data["columna"] == "To Do"
    # La fecha límite se ancla a mediodía en la zona del usuario.
    assert result.data["vence"] == "2026-07-15T12:00:00-05:00"

    call = deck.calls[0]
    assert call["uid"] == "mmazo"
    assert call["board"] == "TECH PROY"
    assert call["stack"] == "To Do"
    assert call["title"] == "Llamar cliente"
    assert call["description"] == "con notas"
    assert call["duedate"] == datetime(2026, 7, 15, 12, 0, tzinfo=BOGOTA)


@pytest.mark.asyncio
async def test_creates_card_without_duedate():
    deck = FakeDeck()
    skill = CrearTarjetaDeckSkill(deck=deck, tz=BOGOTA)

    result = await skill.execute(_args(), _USER)

    assert result.ok
    assert result.data["vence"] is None
    assert deck.calls[0]["duedate"] is None


@pytest.mark.asyncio
async def test_missing_titulo_is_failure():
    deck = FakeDeck()
    skill = CrearTarjetaDeckSkill(deck=deck, tz=BOGOTA)

    result = await skill.execute({"board": "TECH PROY", "columna": "To Do"}, _USER)

    assert not result.ok
    assert "titulo" in result.error
    assert deck.calls == []


@pytest.mark.asyncio
async def test_missing_board_is_failure():
    deck = FakeDeck()
    skill = CrearTarjetaDeckSkill(deck=deck, tz=BOGOTA)

    result = await skill.execute({"titulo": "X", "columna": "To Do"}, _USER)

    assert not result.ok
    assert "board" in result.error.lower()
    assert deck.calls == []


@pytest.mark.asyncio
async def test_missing_columna_is_failure():
    deck = FakeDeck()
    skill = CrearTarjetaDeckSkill(deck=deck, tz=BOGOTA)

    result = await skill.execute({"titulo": "X", "board": "TECH PROY"}, _USER)

    assert not result.ok
    assert "columna" in result.error.lower()
    assert deck.calls == []


@pytest.mark.asyncio
async def test_invalid_fecha_limite_is_failure():
    deck = FakeDeck()
    skill = CrearTarjetaDeckSkill(deck=deck, tz=BOGOTA)

    result = await skill.execute(_args(fecha_limite="15/07/2026"), _USER)

    assert not result.ok
    assert "fecha_limite" in result.error
    assert deck.calls == []


@pytest.mark.asyncio
async def test_port_error_result_becomes_failure():
    deck = FakeDeck(
        created=CreatedCard(
            ok=False,
            status=403,
            error="No se pudo crear la tarjeta (HTTP 403): posible falta de permiso.",
        )
    )
    skill = CrearTarjetaDeckSkill(deck=deck, tz=BOGOTA)

    result = await skill.execute(_args(), _USER)

    assert not result.ok
    assert "403" in result.error


@pytest.mark.asyncio
async def test_port_exception_becomes_failure_not_raised():
    skill = CrearTarjetaDeckSkill(deck=BoomDeck(), tz=BOGOTA)

    result = await skill.execute(_args(), _USER)

    assert not result.ok
    assert "tarjeta" in result.error.lower()


def test_tool_schema_is_public_contract():
    skill = CrearTarjetaDeckSkill(deck=FakeDeck())

    assert skill.name == "crear_tarjeta_deck"
    schema = skill.parameters_schema
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["titulo", "board", "columna"]
    for key in ("titulo", "board", "columna", "descripcion", "fecha_limite"):
        assert key in schema["properties"]

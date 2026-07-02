"""Value objects y parseo de Deck (dominio puro, stdlib, sin I/O) — Bloque 2.3.

El adapter REST (infra) hace el I/O contra la API de Deck
(``/index.php/apps/deck/api/v1.0/``) y **delega aquí** la transformación de formato:
parsear el JSON a :class:`Board`/:class:`Stack`/:class:`Card` y resolver un tablero o
columna por **id o título** (:func:`find_board`/:func:`find_stack`). Mismo patrón de
capas que ``domain.caldav`` para Calendar (ARCHITECTURE §3): el puerto ``DeckPort`` habla
en estos tipos, no en JSON crudo.

NOTA (2.3b): la **asignación de usuarios** a una tarjeta NO se modela aquí — requiere
resolver nombre→uid y queda para el Bloque 2.3b.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Board:
    """Tablero de Deck (mínimo): ``id`` numérico y ``title``."""

    id: int
    title: str


@dataclass(frozen=True)
class Card:
    """Tarjeta de una columna. ``due_date`` es el ISO-8601 crudo de Deck (o ``None``).

    ``assignees`` son los **uids** asignados a la tarjeta (Bloque 2.3, refinamiento).
    Deck los expone en ``assignedUsers[].participant.uid`` (verificado contra
    ``nextcloud/deck`` ``lib/Db/Card.php`` + ``Assignment.php``, no asumido): cada entrada
    es un *Assignment* con un ``participant`` resoluble a usuario. Vacío si nadie está
    asignado. NOTA (2.3b): NO se resuelve pertenencia a grupos/círculos — solo el uid
    directo del participante.
    """

    id: int
    title: str
    description: str | None = None
    due_date: str | None = None
    assignees: tuple[str, ...] = ()


@dataclass(frozen=True)
class Stack:
    """Columna (stack) de un tablero, con sus tarjetas."""

    id: int
    title: str
    cards: tuple[Card, ...] = ()


@dataclass(frozen=True)
class BoardStatus:
    """Un tablero resuelto + sus columnas (con tarjetas). Lo devuelve ``get_board_status``."""

    board: Board
    stacks: tuple[Stack, ...] = ()


@dataclass(frozen=True)
class CreatedCard:
    """Resultado de crear una tarjeta (escritura), **error como dato** (como Calendar 2.2).

    ``ok=True`` con ``card_id`` (y ``url`` best-effort) cuando Deck confirma (HTTP 200/201);
    ``ok=False`` con ``error`` legible ante 400/403/404 u otros — sin excepción cruda.
    """

    ok: bool
    status: int
    card_id: int | None = None
    url: str | None = None
    error: str | None = None


def parse_boards(payload: Any) -> list[Board]:
    """Lista JSON de Deck → ``list[Board]``. Ignora entradas sin ``id`` (defensivo)."""
    boards: list[Board] = []
    for item in payload or []:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        boards.append(Board(id=int(item["id"]), title=str(item.get("title") or "")))
    return boards


def parse_stacks(payload: Any) -> list[Stack]:
    """Lista JSON de stacks (``GET /boards/{id}/stacks``) → ``list[Stack]`` con sus cards."""
    stacks: list[Stack] = []
    for item in payload or []:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        cards = tuple(
            _parse_card(card)
            for card in (item.get("cards") or [])
            if isinstance(card, dict) and card.get("id") is not None
        )
        stacks.append(
            Stack(id=int(item["id"]), title=str(item.get("title") or ""), cards=cards)
        )
    return stacks


def _parse_card(item: dict) -> Card:
    """Normaliza una tarjeta JSON; ``description``/``duedate`` vacíos → ``None``."""
    return Card(
        id=int(item["id"]),
        title=str(item.get("title") or ""),
        description=(item.get("description") or None),
        due_date=(item.get("duedate") or None),
        assignees=_parse_assignees(item.get("assignedUsers")),
    )


def _parse_assignees(raw: object) -> tuple[str, ...]:
    """uids de ``assignedUsers``: cada entrada trae ``participant.uid`` (o ``primaryKey``).

    Defensivo: tolera que ``participant`` sea un dict (resuelto, lo normal) o el string del
    uid (sin resolver), y descarta entradas sin uid.
    """
    uids: list[str] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        participant = entry.get("participant")
        if isinstance(participant, dict):
            uid = participant.get("uid") or participant.get("primaryKey")
        elif isinstance(participant, str):
            uid = participant
        else:
            uid = None
        if uid:
            uids.append(str(uid))
    return tuple(uids)


def parse_created_card_id(payload: Any) -> int | None:
    """``id`` de la tarjeta creada (Deck devuelve el objeto card en el 200/201)."""
    if isinstance(payload, dict) and payload.get("id") is not None:
        return int(payload["id"])
    return None


def find_board(boards: list[Board], ref: str | int) -> Board | None:
    """Resuelve un tablero por **id** (si ``ref`` es numérico) o por **título** (case-insensitive)."""
    text = str(ref).strip()
    if text.isdigit():
        target = int(text)
        return next((b for b in boards if b.id == target), None)
    folded = text.casefold()
    return next((b for b in boards if b.title.casefold() == folded), None)


def find_stack(stacks: list[Stack], ref: str | int) -> Stack | None:
    """Resuelve una columna por **id** (si ``ref`` es numérico) o por **título** (case-insensitive)."""
    text = str(ref).strip()
    if text.isdigit():
        target = int(text)
        return next((s for s in stacks if s.id == target), None)
    folded = text.casefold()
    return next((s for s in stacks if s.title.casefold() == folded), None)


def filter_stacks_by_assignee(stacks: tuple[Stack, ...], uid: str) -> tuple[Stack, ...]:
    """Deja en cada columna solo las tarjetas cuyo ``assignees`` contiene ``uid``.

    Conserva TODAS las columnas (aunque queden sin tarjetas) para no perder la estructura
    del tablero; las tarjetas sin asignados nunca pasan el filtro (Bloque 2.3, "solo mías").
    """
    return tuple(
        Stack(
            id=stack.id,
            title=stack.title,
            cards=tuple(card for card in stack.cards if uid in card.assignees),
        )
        for stack in stacks
    )

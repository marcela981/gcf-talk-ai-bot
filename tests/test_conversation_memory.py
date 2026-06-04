"""Unit tests for app.adapters.in_memory_conversation_memory (ADR-014).

Cubren los invariantes del buffer in-memory por sala: orden cronológico,
eviction FIFO por `max_messages`, expiración por TTL (con reloj inyectado, sin
dormir), aislamiento estricto por `token` y validación de rol. No hay red ni I/O.
"""
from __future__ import annotations

import pytest

from app.adapters.in_memory_conversation_memory import InMemoryConversationMemory
from app.domain.message import Message


class FakeClock:
    """Reloj monotónico determinista para probar el TTL sin dormir el test."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _mem(**kwargs) -> InMemoryConversationMemory:
    defaults = {"max_messages": 10, "ttl_seconds": 3600}
    defaults.update(kwargs)
    return InMemoryConversationMemory(**defaults)


def test_history_is_empty_for_unknown_room():
    mem = _mem()
    assert mem.history("nope") == []


def test_records_preserve_chronological_order():
    mem = _mem()
    mem.record("room1", "user", "users/alice", "hola")
    mem.record("room1", "assistant", "IA", "buenas")
    mem.record("room1", "user", "users/alice", "¿qué tal?")

    assert mem.history("room1") == [
        Message(role="user", content="hola"),
        Message(role="assistant", content="buenas"),
        Message(role="user", content="¿qué tal?"),
    ]


def test_fifo_eviction_drops_oldest_beyond_max_messages():
    mem = _mem(max_messages=3)
    for i in range(5):
        mem.record("room1", "user", "users/alice", f"msg{i}")

    contents = [m.content for m in mem.history("room1")]
    # Solo sobreviven los 3 más recientes; los 2 más antiguos se descartan (FIFO).
    assert contents == ["msg2", "msg3", "msg4"]


def test_ttl_expires_entries_on_read():
    clock = FakeClock()
    mem = _mem(ttl_seconds=100, clock=clock)
    mem.record("room1", "user", "users/alice", "viejo")

    clock.advance(101)
    assert mem.history("room1") == []  # expiró por TTL


def test_ttl_keeps_fresh_entries_and_drops_only_expired():
    clock = FakeClock()
    mem = _mem(ttl_seconds=100, clock=clock)
    mem.record("room1", "user", "users/alice", "viejo")

    clock.advance(101)  # "viejo" ya expiró
    mem.record("room1", "user", "users/alice", "nuevo")

    contents = [m.content for m in mem.history("room1")]
    assert contents == ["nuevo"]  # solo el vigente, el expirado se purgó


def test_strict_isolation_between_tokens():
    mem = _mem()
    mem.record("roomA", "user", "users/alice", "secreto A")
    mem.record("roomB", "user", "users/bob", "cosa B")

    assert [m.content for m in mem.history("roomA")] == ["secreto A"]
    assert [m.content for m in mem.history("roomB")] == ["cosa B"]


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_allowed_roles_are_accepted(role):
    mem = _mem()
    mem.record("room1", role, "autor", "texto")
    assert mem.history("room1")[0].role == role


@pytest.mark.parametrize("role", ["system", "tool", "", "User"])
def test_invalid_roles_are_rejected(role):
    mem = _mem()
    with pytest.raises(ValueError):
        mem.record("room1", role, "autor", "texto")

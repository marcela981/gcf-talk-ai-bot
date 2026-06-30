"""Unit tests for app.domain.identity (ADR-016): actor_id → uid impersonable.

Lógica pura, sin red. Cubre el mapeo de la tabla de ADR-016: solo `users/<uid>`
es impersonable; invitados, federados, bots y vacío devuelven None.
"""
from __future__ import annotations

import pytest

from app.domain.identity import resolve_impersonated_uid


@pytest.mark.parametrize(
    "actor_id, expected",
    [
        ("users/alice", "alice"),
        ("users/mmazo", "mmazo"),
        ("users/John.Doe-1", "John.Doe-1"),
        ("guests/abc123", None),
        ("federated_users/bob@otra.nube", None),
        ("bridged/whatever", None),
        ("bots/gcf-ai", None),
        ("", None),
        ("users/", None),  # prefijo sin uid
        ("users/   ", None),  # solo espacios
        ("alice", None),  # sin prefijo reconocido
    ],
)
def test_resolve_impersonated_uid(actor_id: str, expected: str | None):
    assert resolve_impersonated_uid(actor_id) == expected

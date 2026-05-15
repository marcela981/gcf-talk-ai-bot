"""Unit tests for app.domain.prompt_builder.

These tests pin two invariants of the Layered System Prompt design:

* L0 is structurally indelible — there is no caller-supplied path that can
  remove, replace or precede it.
* `extra_system` is a strictly additive slot for L1/L2 in Fase 2; it never
  alters L0.
"""
from __future__ import annotations

from app.domain.message import Message
from app.domain.prompt_builder import L0_CORE_SYSTEM_PROMPT, build_messages


def test_l0_is_always_first_message():
    msgs = build_messages(user_text="hola")
    assert msgs[0].role == "system"
    assert msgs[0].content == L0_CORE_SYSTEM_PROMPT


def test_l0_cannot_be_overridden_via_extra_system():
    msgs = build_messages(user_text="hola", extra_system=["custom prompt"])
    assert msgs[0] == Message(role="system", content=L0_CORE_SYSTEM_PROMPT)
    assert msgs[1] == Message(role="system", content="custom prompt")
    assert msgs[2] == Message(role="user", content="hola")


def test_extra_system_none_yields_only_l0_and_user():
    msgs = build_messages(user_text="hola")
    assert msgs == [
        Message(role="system", content=L0_CORE_SYSTEM_PROMPT),
        Message(role="user", content="hola"),
    ]


def test_extra_system_empty_list_yields_only_l0_and_user():
    msgs = build_messages(user_text="hola", extra_system=[])
    assert msgs == [
        Message(role="system", content=L0_CORE_SYSTEM_PROMPT),
        Message(role="user", content="hola"),
    ]


def test_extra_system_multiple_items_preserves_order():
    msgs = build_messages(user_text="hola", extra_system=["A", "B"])
    assert [m.role for m in msgs] == ["system", "system", "system", "user"]
    assert msgs[0].content == L0_CORE_SYSTEM_PROMPT
    assert msgs[1].content == "A"
    assert msgs[2].content == "B"
    assert msgs[3].content == "hola"


def test_l0_contains_critical_identity_anchors():
    for anchor in (
        "GCF",
        "Global Corporate Financial",
        "MMC",
        "Nextcloud",
        "fuera de mi alcance",
    ):
        assert anchor in L0_CORE_SYSTEM_PROMPT, f"L0 perdió el anchor {anchor!r}"

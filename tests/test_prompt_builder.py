"""Unit tests for app.domain.prompt_builder."""
from __future__ import annotations

from app.domain.message import Message
from app.domain.prompt_builder import DEFAULT_SYSTEM_PROMPT, build_messages


def test_default_system_prompt_then_user():
    msgs = build_messages(user_text="hola")
    assert len(msgs) == 2
    assert msgs[0] == Message(role="system", content=DEFAULT_SYSTEM_PROMPT)
    assert msgs[1] == Message(role="user", content="hola")


def test_custom_system_prompt_replaces_default():
    msgs = build_messages(user_text="¿qué hora es?", system_prompt="Eres un reloj.")
    assert msgs[0].role == "system"
    assert msgs[0].content == "Eres un reloj."
    assert msgs[1].role == "user"
    assert msgs[1].content == "¿qué hora es?"


def test_order_is_system_then_user_with_correct_roles():
    msgs = build_messages(user_text="x")
    assert [m.role for m in msgs] == ["system", "user"]


def test_empty_system_prompt_is_respected():
    msgs = build_messages(user_text="x", system_prompt="")
    assert msgs[0].content == ""
    assert msgs[0].role == "system"

"""Unit tests for the pure trigger policy in app.domain.message_policy."""
from __future__ import annotations

from app.domain.message_policy import should_reply, strip_mention


BOT = "GCF AI Bot"


def _call(
    *,
    raw_text: str = "@GCF AI Bot hola",
    actor_id: str = "users/alice",
    object_name: str = "message",
    bot_display_name: str = BOT,
) -> bool:
    return should_reply(
        raw_text=raw_text,
        actor_id=actor_id,
        object_name=object_name,
        bot_display_name=bot_display_name,
    )


def test_rejects_non_message_event():
    assert _call(object_name="reaction") is False


def test_rejects_bot_actor_to_prevent_loops():
    assert _call(actor_id="bots/other-bot") is False


def test_rejects_empty_text_after_strip():
    assert _call(raw_text="   \n\t  ") is False


def test_accepts_mention_with_exact_casing():
    assert _call(raw_text="@GCF AI Bot hola") is True


def test_mention_is_case_insensitive():
    assert _call(raw_text="oye @gcf ai bot, una pregunta") is True
    assert _call(raw_text="@GCF AI BOT ping") is True


def test_mention_allows_quotes_around_name():
    assert _call(raw_text='hola @"GCF AI Bot" ¿estás ahí?') is True


def test_mention_tolerates_extra_spacing_between_words():
    assert _call(raw_text="@GCF   AI    Bot resume esto") is True


def test_rejects_message_without_mention():
    assert _call(raw_text="hola equipo, ¿cómo van?") is False


def test_rejects_partial_name_without_at_sign():
    assert _call(raw_text="GCF AI Bot deberia ayudarnos") is False


def test_strip_mention_removes_mention_and_collapses_whitespace():
    assert strip_mention("@GCF AI Bot hola", BOT) == "hola"
    assert strip_mention("hey @GCF AI Bot how are you?", BOT) == "hey how are you?"
    assert strip_mention('@"GCF AI Bot"   resume esto', BOT) == "resume esto"


def test_strip_mention_is_noop_when_no_mention():
    assert strip_mention("solo texto", BOT) == "solo texto"

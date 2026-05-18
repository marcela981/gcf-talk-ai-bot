"""Unit tests for the pure trigger policy in app.domain.message_policy."""
from __future__ import annotations

from app.domain.message_policy import should_reply, strip_mention


MENTION = "IA"


def _call(
    *,
    raw_text: str = "@IA hola",
    actor_id: str = "users/alice",
    object_name: str = "message",
    bot_mention_name: str = MENTION,
) -> bool:
    return should_reply(
        raw_text=raw_text,
        actor_id=actor_id,
        object_name=object_name,
        bot_mention_name=bot_mention_name,
    )


def test_rejects_non_message_event():
    assert _call(object_name="reaction") is False


def test_rejects_bot_actor_to_prevent_loops():
    assert _call(actor_id="bots/other-bot") is False


def test_rejects_empty_text_after_strip():
    assert _call(raw_text="   \n\t  ") is False


def test_accepts_mention_with_exact_casing():
    assert _call(raw_text="@IA hola") is True


def test_mention_accepts_lowercase():
    assert _call(raw_text="@ia hola") is True


def test_mention_accepts_mixed_case():
    assert _call(raw_text="@Ia hola") is True
    assert _call(raw_text="@iA hola") is True


def test_mention_with_quotes():
    assert _call(raw_text='@"IA" hola') is True


def test_rejects_message_without_mention():
    assert _call(raw_text="hola equipo, ¿cómo van?") is False


def test_rejects_partial_name_without_at_sign():
    assert _call(raw_text="IA deberia ayudarnos") is False


def test_short_name_not_embedded():
    assert _call(raw_text="@IAtech está roto") is False


def test_short_name_not_in_word():
    assert _call(raw_text="vIAje largo") is False


def test_strip_mention_removes_mention_and_collapses_whitespace():
    assert strip_mention("@IA hola", MENTION) == "hola"
    assert strip_mention("hey @IA how are you?", MENTION) == "hey how are you?"
    assert strip_mention('@"IA"   resume esto', MENTION) == "resume esto"


def test_strip_mention_is_noop_when_no_mention():
    assert strip_mention("solo texto", MENTION) == "solo texto"

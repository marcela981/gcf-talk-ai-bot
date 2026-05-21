"""Unit tests for the pure trigger policy in app.domain.message_policy."""
from __future__ import annotations

from app.domain.message_policy import should_reply, strip_invocation


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


def test_accepts_prefix_lowercase():
    assert _call(raw_text="/ai ¿qué hora es?") is True


def test_accepts_prefix_uppercase():
    assert _call(raw_text="/AI hola") is True


def test_accepts_prefix_mixed_case():
    assert _call(raw_text="/Ai ping") is True


def test_accepts_prefix_with_leading_spaces():
    assert _call(raw_text="   /ai algo") is True


def test_accepts_prefix_with_newline_separator():
    assert _call(raw_text="/ai\n¿algo?") is True


def test_rejects_prefix_without_content():
    assert _call(raw_text="/ai") is False


def test_rejects_prefix_glued_to_word():
    assert _call(raw_text="/airbnb consulta") is False


def test_rejects_prefix_not_at_start():
    assert _call(raw_text="hola /ai algo") is False


def test_strip_invocation_removes_mention_and_collapses_whitespace():
    assert strip_invocation("@IA hola", MENTION) == "hola"
    assert strip_invocation("hey @IA how are you?", MENTION) == "hey how are you?"
    assert strip_invocation('@"IA"   resume esto', MENTION) == "resume esto"


def test_strip_invocation_is_noop_when_no_trigger():
    assert strip_invocation("solo texto", MENTION) == "solo texto"


def test_strip_invocation_removes_prefix():
    assert strip_invocation("/ai pregunta", MENTION) == "pregunta"


def test_strip_invocation_removes_prefix_with_leading_spaces():
    assert strip_invocation("   /ai  hola", MENTION) == "hola"


def test_strip_invocation_still_removes_mention():
    assert strip_invocation("@IA resume esto", MENTION) == "resume esto"

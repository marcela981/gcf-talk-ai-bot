"""Pure trigger policy: decide whether the bot should reply to a Talk event.

No I/O, no logging — keep this trivially testable.
"""
from __future__ import annotations

import re


# Trailing space is intentional: it documents that `/ai` must be followed by a
# separator so lookalikes such as `/airbnb` or `/aireos` cannot fire the bot.
# The compiled pattern below enforces the same invariant against any whitespace
# (space, tab, newline), not only the literal space.
AI_PREFIX = "/ai "

_AI_PREFIX_TOKEN = AI_PREFIX.rstrip()
_AI_PREFIX_PATTERN = re.compile(
    rf"^\s*{re.escape(_AI_PREFIX_TOKEN)}\s",
    re.IGNORECASE,
)


def should_reply(
    *,
    raw_text: str,
    actor_id: str,
    object_name: str,
    bot_mention_name: str,
) -> bool:
    if object_name != "message":
        return False
    if actor_id.startswith("bots/"):
        return False
    if not raw_text.strip():
        return False
    if _mention_pattern(bot_mention_name).search(raw_text) is not None:
        return True
    if _has_prefix(raw_text):
        return True
    return False


def strip_invocation(text: str, bot_mention_name: str) -> str:
    """Remove the trigger token (prefix or @mention) and return the payload."""
    if _has_prefix(text):
        return _strip_prefix(text)
    return _strip_mention(text, bot_mention_name)


def _has_prefix(text: str) -> bool:
    return _AI_PREFIX_PATTERN.search(text) is not None


def _strip_prefix(text: str) -> str:
    return _AI_PREFIX_PATTERN.sub("", text, count=1).strip()


def _strip_mention(text: str, bot_mention_name: str) -> str:
    pattern = _mention_pattern(bot_mention_name)
    cleaned = pattern.sub(" ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _mention_pattern(bot_mention_name: str) -> re.Pattern[str]:
    parts = bot_mention_name.split()
    if not parts:
        return _NEVER_MATCH
    escaped_name = r"\s+".join(re.escape(p) for p in parts)
    # Trailing lookahead avoids matching the trigger when it's glued to more
    # word characters (e.g. "@IAtech" must not fire "@IA").
    return re.compile(
        rf'@["\']?{escaped_name}["\']?(?=\s|$|[^\w])',
        re.IGNORECASE,
    )


_NEVER_MATCH = re.compile(r"(?!x)x")

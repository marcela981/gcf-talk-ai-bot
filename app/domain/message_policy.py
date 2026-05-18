"""Pure trigger policy: decide whether the bot should reply to a Talk event.

No I/O, no logging — keep this trivially testable.
"""
from __future__ import annotations

import re


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
    return False


def strip_mention(text: str, bot_mention_name: str) -> str:
    return _strip_mention(text, bot_mention_name)


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

"""Handle authenticated Talk chat events.

Week 1 POC: reply "pong" to "ping". The OpenAI scaffolding in
`app.services.llm_client` is wired into the project but intentionally not
called yet — that integration lands in Week 2.
"""
from __future__ import annotations

import logging

from nc_py_api import talk_bot

logger = logging.getLogger(__name__)


async def handle_message(message: talk_bot.TalkBotMessage) -> None:
    # Talk fires the webhook for many event kinds (joins, leaves, reactions,
    # ...). Only chat messages have a user-typed body to act on.
    if message.object_name != "message":
        return

    # Never reply to other bots or to ourselves — that's how infinite loops
    # start. Talk encodes bot actors as "bots/<id>".
    if message.actor_id.startswith("bots/"):
        return

    content = message.object_content or {}
    user_text = (content.get("message") or "").strip()

    logger.info(
        "Talk message in %s from %s: %r",
        message.conversation_token,
        message.actor_id,
        user_text,
    )

    if user_text.lower() == "ping":
        message.send_message("pong")

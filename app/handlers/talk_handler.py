"""Adapter that bridges Talk webhook events to the ConversationService."""
from __future__ import annotations

import logging

from nc_py_api import talk_bot

from app.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)


async def handle_message(
    message: talk_bot.TalkBotMessage,
    service: ConversationService,
    bot: talk_bot.AsyncTalkBot,
) -> None:
    content = message.object_content or {}
    user_text = content.get("message") or ""

    reply = await service.handle(
        raw_text=user_text,
        actor_id=message.actor_id,
        object_name=message.object_name,
    )

    logger.info(
        "Talk event conv=%s actor=%s object=%s reply=%s",
        message.conversation_token,
        message.actor_id,
        message.object_name,
        "yes" if reply is not None else "no",
    )

    if reply is not None:
        await bot.send_message(reply, message)
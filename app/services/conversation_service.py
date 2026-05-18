"""Use case: turn an inbound Talk event into an optional reply string.

Owns the trigger decision, prompt assembly, LLM invocation, and the
user-facing fallback when the adapter fails.
"""
from __future__ import annotations

import logging

from app.adapters.openai_adapter import LLMError
from app.domain.message_policy import should_reply, strip_mention
from app.domain.prompt_builder import build_messages
from app.services.llm_port import LLMPort

logger = logging.getLogger(__name__)

_FALLBACK_MSG = (
    "Lo siento, no pude procesar tu mensaje en este momento. "
    "Inténtalo de nuevo en unos segundos."
)


class ConversationService:
    def __init__(self, llm: LLMPort, bot_mention_name: str) -> None:
        self._llm = llm
        self._bot_mention_name = bot_mention_name

    async def handle(
        self,
        *,
        raw_text: str,
        actor_id: str,
        object_name: str,
    ) -> str | None:
        if not should_reply(
            raw_text=raw_text,
            actor_id=actor_id,
            object_name=object_name,
            bot_mention_name=self._bot_mention_name,
        ):
            return None

        clean = strip_mention(raw_text, self._bot_mention_name)
        messages = build_messages(user_text=clean)
        try:
            return await self._llm.complete(messages)
        except LLMError:
            logger.exception("LLM completion failed; returning fallback reply.")
            return _FALLBACK_MSG

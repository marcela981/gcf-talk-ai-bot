"""Entry point for the GCF Talk AI Bot ExApp.

Wires together:
  * FastAPI app with AppAPI's authentication middleware (verifies the shared
    secret on every request coming from Nextcloud).
  * AppAPI lifecycle endpoints (/init, /enabled, /heartbeat) registered via
    `set_handlers`.
  * A Talk bot webhook with automatic HMAC-SHA256 signature verification
    through the `atalk_bot_msg` dependency.

ASYNC MIGRATION (nc_py_api 0.30.x):
  nc_py_api >= 0.30 deprecated the synchronous `TalkBot` / sync lifecycle
  handlers. Under a FastAPI async `lifespan`, a *synchronous* enabled_handler
  is not awaited correctly by `set_handlers`. Hence the full async path:
    * talk_bot.TalkBot          -> talk_bot.AsyncTalkBot
    * def enabled_handler        -> async def enabled_handler
    * NextcloudApp               -> AsyncNextcloudApp

TALK BOT REGISTRATION API:
  The bot is registered with Talk through the *NextcloudApp* object, NOT the
  AsyncTalkBot object. AsyncTalkBot only carries identity (callback_url,
  display_name, description) and is used to *receive/answer* messages. The
  registration verbs live on `nc`:
    * nc.register_talk_bot(callback_url, display_name, description)
        -> registers the bot, AppAPI writes a row in appconfig_ex
    * nc.unregister_talk_bot(callback_url)
        -> removes it
  An earlier revision called BOT.enable_bot(nc), which never existed on
  AsyncTalkBot (AttributeError at enable time): the ExApp showed [enabled]
  in AppAPI but never appeared in `talk:bot:list`.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import Response

from nc_py_api import AsyncNextcloudApp, talk_bot
from nc_py_api.ex_app import (
    AppAPIAuthMiddleware,
    atalk_bot_msg,
    run_app,
    set_handlers,
)

from app.adapters.openai_adapter import OpenAIAdapter
from app.config import settings
from app.handlers.talk_handler import handle_message
from app.services.conversation_service import ConversationService


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


_BOT_CALLBACK_URL = "/talk_bot"

# AsyncTalkBot carries the bot's identity and is the object used to *answer*
# messages (atalk_bot_msg yields TalkBotMessage; .send_message replies).
# It does NOT register itself — that is done via AsyncNextcloudApp below.
BOT = talk_bot.AsyncTalkBot(
    callback_url=_BOT_CALLBACK_URL,
    display_name=settings.bot_display_name,
    description=settings.bot_description,
)

# Built at import time. The adapter tolerates an empty api_key here and only
# raises when `complete()` is actually invoked, so import never fails because
# OPENAI_API_KEY is unset in some environments (e.g. CI, local checks).
_adapter = OpenAIAdapter(
    api_key=settings.openai_api_key,
    default_model=settings.openai_model,
)
_service = ConversationService(
    llm=_adapter,
    bot_display_name=settings.bot_display_name,
)


async def enabled_handler(enabled: bool, nc: AsyncNextcloudApp) -> str:
    """Invoked by AppAPI when the operator enables or disables the ExApp.

    Registers (or unregisters) the bot with the Talk app so Nextcloud knows
    where to deliver chat webhooks. AppAPI expects an empty string on
    success or an error message on failure.

    Registration goes through `nc` (AsyncNextcloudApp), not through BOT:
      * register_talk_bot writes the callback + signing secret into Talk;
        after this the bot shows up in `occ talk:bot:list`.
      * unregister_talk_bot removes it on disable.
    """
    try:
        if enabled:
            await nc.register_talk_bot(
                _BOT_CALLBACK_URL,
                settings.bot_display_name,
                settings.bot_description,
            )
            logger.info("Bot registered with Talk.")
        else:
            await nc.unregister_talk_bot(_BOT_CALLBACK_URL)
            logger.info("Bot unregistered from Talk.")
    except Exception as exc:
        logger.exception("enabled_handler failed")
        return str(exc)
    return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    # set_handlers wires the AppAPI lifecycle routes onto `app`:
    #   POST /init       — initialization hook
    #   PUT  /enabled    — calls enabled_handler(enabled: bool, nc)
    #   GET  /heartbeat  — liveness probe (used by AppAPI and Docker)
    set_handlers(app, enabled_handler)
    yield


APP = FastAPI(lifespan=lifespan)
# Validates every inbound request against the ExApp shared secret + headers
# AppAPI sets (EX-APP-ID, AUTHORIZATION-APP-API, ...). Requests that don't
# come from a trusted Nextcloud instance are rejected with HTTP 401.
APP.add_middleware(AppAPIAuthMiddleware)


@APP.post(_BOT_CALLBACK_URL)
async def talk_bot_webhook(
    message: Annotated[talk_bot.TalkBotMessage, Depends(atalk_bot_msg)],
) -> Response:
    """Talk delivers chat events here.

    `atalk_bot_msg` is the security boundary for this route:
      1. Reads X-Nextcloud-Talk-Random and X-Nextcloud-Talk-Signature.
      2. Recomputes HMAC-SHA256(secret, random + body) and constant-time
         compares it against the provided signature.
      3. Rejects with HTTP 401 if the signature is missing or invalid.

    By the time the body of this function runs, the message is authenticated.
    """
    await handle_message(message, _service)
    return Response(status_code=200)


if __name__ == "__main__":
    # `run_app` reads APP_HOST / APP_PORT from the environment, which AppAPI
    # injects at deploy time (or .env in local development).
    run_app("app.main:APP", log_level="info")
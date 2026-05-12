"""Entry point for the GCF Talk AI Bot ExApp.

Wires together:
  * FastAPI app with AppAPI's authentication middleware (verifies the shared
    secret on every request coming from Nextcloud).
  * AppAPI lifecycle endpoints (/init, /enabled, /heartbeat) registered via
    `set_handlers`.
  * A Talk bot webhook with automatic HMAC-SHA256 signature verification
    through the `atalk_bot_msg` dependency.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import Response

from nc_py_api import NextcloudApp, talk_bot
from nc_py_api.ex_app import (
    AppAPIAuthMiddleware,
    atalk_bot_msg,
    run_app,
    set_handlers,
)

from app.config import settings
from app.handlers.talk_handler import handle_message


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


BOT = talk_bot.TalkBot(
    callback_url="/talk_bot",
    display_name=settings.bot_display_name,
    description=settings.bot_description,
)


def enabled_handler(enabled: bool, nc: NextcloudApp) -> str:
    """Invoked by AppAPI when the operator enables or disables the ExApp.

    Registers (or unregisters) the bot with the Talk app so Nextcloud knows
    where to deliver chat webhooks. AppAPI expects an empty string on
    success or an error message on failure.
    """
    try:
        if enabled:
            BOT.enable_bot(nc)
            logger.info("Bot registered with Talk.")
        else:
            BOT.disable_bot(nc)
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


@APP.post("/talk_bot")
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
    await handle_message(message)
    return Response(status_code=200)


if __name__ == "__main__":
    # `run_app` reads APP_HOST / APP_PORT from the environment, which AppAPI
    # injects at deploy time (or .env in local development).
    run_app("app.main:APP", log_level="info")

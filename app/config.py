"""Application configuration.

Reads runtime settings from environment variables. AppAPI injects most of the
infrastructure variables (APP_ID, APP_SECRET, APP_PORT, NEXTCLOUD_URL, ...)
at deployment time; nc_py_api consumes those directly. The operator only has
to set the OpenAI credentials and the bot's display strings.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Best-effort .env loading for local development. In the container, AppAPI
# is the source of truth and python-dotenv is a no-op.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


@dataclass(frozen=True)
class Settings:
    bot_display_name: str
    bot_description: str
    openai_api_key: str
    openai_model: str


def _load() -> Settings:
    return Settings(
        bot_display_name=os.environ.get("BOT_DISPLAY_NAME", "GCF AI Bot"),
        bot_description=os.environ.get(
            "BOT_DESCRIPTION",
            "AI-powered assistant using OpenAI ChatGPT.",
        ),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    )


settings = _load()

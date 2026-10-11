"""Deployment configuration — secrets and addresses ONLY.

Models, weights, thresholds and limits live in constants.py as reviewable code.
"""

from __future__ import annotations

import os


class Settings:
    LOGFIRE_TOKEN: str | None = os.environ.get("LOGFIRE_TOKEN") or None

    # The interpretation model is reached through one of two providers; both are
    # read here and chosen in `agent/llm.py`, the only place a provider is named.
    OPENROUTER_API_KEY: str | None = os.environ.get("OPENROUTER_API_KEY") or None
    ANTHROPIC_API_KEY: str | None = os.environ.get("ANTHROPIC_API_KEY") or None
    ANTHROPIC_BASE_URL: str | None = os.environ.get("ANTHROPIC_BASE_URL") or None

    HOST: str = os.environ.get("AGENT_HOST", "0.0.0.0")
    PORT: int = int(os.environ.get("AGENT_PORT", "8080"))
    DSN: str = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")
    VALHALLA_URL: str = os.environ.get("VALHALLA_URL", "http://localhost:8002")


settings = Settings()

DSN = settings.DSN
VALHALLA_URL = settings.VALHALLA_URL
HOST = settings.HOST
PORT = settings.PORT
LOGFIRE_TOKEN = settings.LOGFIRE_TOKEN


def openrouter_api_key() -> str | None:
    """The OpenRouter key for this process, or None.

    Read from the environment on every call, with the import-time settings as a fallback.
    """
    return os.environ.get("OPENROUTER_API_KEY") or settings.OPENROUTER_API_KEY


def anthropic_api_key() -> str | None:
    """The Anthropic key (or gateway token) for this process, or None."""
    return os.environ.get("ANTHROPIC_API_KEY") or settings.ANTHROPIC_API_KEY


def anthropic_base_url() -> str | None:
    """The gateway the Anthropic SDK should talk to, or None for the default.

    Set this when the Anthropic-format endpoint is a proxy rather than
    api.anthropic.com.
    """
    return os.environ.get("ANTHROPIC_BASE_URL") or settings.ANTHROPIC_BASE_URL

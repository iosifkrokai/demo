"""Deployment configuration — secrets and addresses ONLY.

Models, weights, thresholds and limits live in constants.py as reviewable code.
"""

from __future__ import annotations

import os


class Settings:
    OPENROUTER_API_KEY: str | None = os.environ.get("OPENROUTER_API_KEY") or None

    LANGFUSE_HOST: str | None = os.environ.get("LANGFUSE_HOST")
    LANGFUSE_PUBLIC_KEY: str | None = os.environ.get("LANGFUSE_PUBLIC_KEY")
    LANGFUSE_SECRET_KEY: str | None = os.environ.get("LANGFUSE_SECRET_KEY")

    HOST: str = os.environ.get("AGENT_HOST", "0.0.0.0")
    PORT: int = int(os.environ.get("AGENT_PORT", "8080"))
    DSN: str = os.environ.get(
        "DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno"
    )
    VALHALLA_URL: str = os.environ.get("VALHALLA_URL", "http://localhost:8002")


settings = Settings()

DSN = settings.DSN
VALHALLA_URL = settings.VALHALLA_URL
HOST = settings.HOST
PORT = settings.PORT


def openrouter_api_key() -> str | None:
    """The OpenRouter key for this process, or None.

    Read from the environment on every call, with the import-time settings as a fallback.
    """
    return os.environ.get("OPENROUTER_API_KEY") or settings.OPENROUTER_API_KEY


def langfuse_configured() -> bool:
    """True when both Langfuse keys are present (a host alone is not enough)."""
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY") or settings.LANGFUSE_PUBLIC_KEY
    ) and bool(os.environ.get("LANGFUSE_SECRET_KEY") or settings.LANGFUSE_SECRET_KEY)

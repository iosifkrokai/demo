"""Deployment configuration — secrets and addresses ONLY."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DSN = "postgresql://grodno:grodno@localhost:5432/grodno"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"


@dataclass
class Settings:
    """The addresses that do not change while the process runs."""

    HOST: str = os.environ.get("AGENT_HOST", "0.0.0.0")
    PORT: int = int(os.environ.get("AGENT_PORT", "8080"))
    DSN: str = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    VALHALLA_URL: str = os.environ.get("VALHALLA_URL", "http://localhost:8002")


settings = Settings()


def logfire_token() -> str | None:
    """The Logfire token, or None — tracing is a no-op without it."""
    return os.environ.get("LOGFIRE_TOKEN") or None


@dataclass(frozen=True)
class Provider:
    """One way to reach a model: where it is, with which key, under which name."""

    name: str
    api_key: str | None
    base_url: str | None
    default_model: str


def chosen_provider() -> Provider:
    """The endpoint this process talks to, as the environment describes it."""
    return Provider(
        name="openrouter",
        api_key=os.environ.get("OPENROUTER_API_KEY") or None,
        base_url=os.environ.get("OPENROUTER_BASE_URL") or None,
        default_model=DEFAULT_MODEL,
    )


def model_name() -> str:
    """The model to ask for: `LLM_MODEL`, or the chosen provider's own default."""
    return os.environ.get("LLM_MODEL") or chosen_provider().default_model

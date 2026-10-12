"""Deployment configuration — secrets and addresses ONLY.

Models, weights, thresholds and limits live in constants.py as reviewable code.

Everything here is read from the environment on every call rather than frozen at
import: a container that starts before its keys are injected, and a test that
injects them, must both see what is actually set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    """The addresses that do not change while the process runs."""

    HOST: str = os.environ.get("AGENT_HOST", "0.0.0.0")
    PORT: int = int(os.environ.get("AGENT_PORT", "8080"))
    DSN: str = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")
    VALHALLA_URL: str = os.environ.get("VALHALLA_URL", "http://localhost:8002")


settings = Settings()

DSN = settings.DSN
VALHALLA_URL = settings.VALHALLA_URL
HOST = settings.HOST
PORT = settings.PORT


def logfire_token() -> str | None:
    """The Logfire token, or None — tracing is a no-op without it."""
    return os.environ.get("LOGFIRE_TOKEN") or None


# --- the model -----------------------------------------------------------------


@dataclass(frozen=True)
class Provider:
    """One way to reach a model: where it is, with which key, under which name."""

    name: str
    api_key: str | None
    base_url: str | None
    default_model: str


@dataclass(frozen=True)
class _Spec:
    """Where one provider's settings live, and what it answers to by default."""

    key_env: str
    base_env: str
    default_model: str


# The whole vocabulary: a key, an endpoint and a default model per provider.
# A model name is written here and nowhere else in the codebase.
#
# One entry, and it is one entry on purpose rather than by accident: every
# endpoint in this vocabulary speaks the OpenAI chat format, so `agent/llm.py`
# has a single client and the word "openrouter" names *the OpenAI-shaped
# endpoint* — OpenRouter itself, a gateway in front of it, or OpenAI proper.
# A provider that speaks a different format (Anthropic's Messages API was the
# one that used to be here) is a second client, not a second key, and it earns
# its place only when a deployment reaches a model through it.
_SPECS: dict[str, _Spec] = {
    "openrouter": _Spec(
        key_env="OPENROUTER_API_KEY",
        base_env="OPENROUTER_BASE_URL",
        default_model="deepseek/deepseek-v4.1-flash",
    ),
}

DEFAULT_PROVIDER = "openrouter"


def provider_names() -> tuple[str, ...]:
    """Every provider this build can talk to."""
    return tuple(_SPECS)


def provider(name: str) -> Provider:
    """One provider, as the environment currently describes it."""
    spec = _SPECS[name]
    return Provider(
        name=name,
        api_key=os.environ.get(spec.key_env) or None,
        base_url=os.environ.get(spec.base_env) or None,
        default_model=spec.default_model,
    )


def chosen_provider() -> Provider:
    """Which provider this process talks to.

    `LLM_PROVIDER` wins; otherwise the provider that actually holds a key
    decides, so a deployment that sets one key needs no further configuration.
    """
    named = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if named in _SPECS:
        return provider(named)
    for name in _SPECS:
        candidate = provider(name)
        if candidate.api_key:
            return candidate
    return provider(DEFAULT_PROVIDER)


def model_name() -> str:
    """The model to ask for: `LLM_MODEL`, or the chosen provider's own default."""
    return os.environ.get("LLM_MODEL") or chosen_provider().default_model

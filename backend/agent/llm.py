"""The single place that names a provider and a model.

Two providers are supported, both reached through PydanticAI:

* **OpenRouter** (`openrouter`) — the default, an OpenAI-shaped gateway.
* **Anthropic** (`anthropic`) — the Anthropic Messages format, which also covers a
  self-hosted gateway speaking that format: point `ANTHROPIC_BASE_URL` at it.

Both SDKs are declared dependencies, so they are imported outright: an absent SDK
is a broken install, and failing at import says so loudly. That is a different
thing from the *layer being unusable*, which is about the key and is answered by
`agent.client.available`.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from core.config import anthropic_api_key, anthropic_base_url, openrouter_api_key

PROVIDERS = ("openrouter", "anthropic")

DEFAULT_MODEL_BY_PROVIDER = {
    "openrouter": "deepseek/deepseek-v4.1-flash",
    "anthropic": "claude-opus-5-5",
}


def provider() -> str:
    """Which provider this process talks to.

    `AGENT_MODEL_PROVIDER` wins; otherwise the key that is actually present
    decides, so a deployment that sets only an Anthropic key gets Anthropic
    without further configuration.
    """
    chosen = (os.environ.get("AGENT_MODEL_PROVIDER") or "").strip().lower()
    if chosen in PROVIDERS:
        return chosen
    if anthropic_api_key() and not openrouter_api_key():
        return "anthropic"
    return "openrouter"


def api_key() -> str | None:
    """The key for the chosen provider, or None."""
    return anthropic_api_key() if provider() == "anthropic" else openrouter_api_key()


def model_name() -> str:
    """The model id, overridable per deployment and defaulting per provider."""
    return os.environ.get("AGENT_INTERPRET_MODEL") or DEFAULT_MODEL_BY_PROVIDER[provider()]


def make_model() -> Any:
    """Build the model for the chosen provider. Tests replace this with a fake."""

    which = provider()
    key = api_key()
    if not key:
        raise RuntimeError(f"no API key for {which}")  # pragma: no cover — checked earlier
    if which == "anthropic":
        return AnthropicModel(
            model_name(),
            provider=AnthropicProvider(api_key=key, base_url=anthropic_base_url()),
        )
    return OpenRouterModel(model_name(), provider=OpenRouterProvider(api_key=key))

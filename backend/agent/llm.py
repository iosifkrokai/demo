"""The single place that names a provider and a model."""

from __future__ import annotations

from typing import Any

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from core.config import Provider, chosen_provider, model_name, provider_names

__all__ = ["chosen_provider", "make_model", "model_name", "provider_names"]


def _provider(chosen: Provider) -> OpenAIProvider:
    """The client for the chosen endpoint, which may be a plain vendor or a gateway."""
    return OpenAIProvider(api_key=chosen.api_key, base_url=chosen.base_url)


def make_model() -> Any:
    """Build the model for the configured provider. Tests replace this with a fake."""
    chosen = chosen_provider()
    if not chosen.api_key:
        raise RuntimeError(f"no API key for {chosen.name}")
    return OpenAIChatModel(model_name(), provider=_provider(chosen))

"""The single place that names a provider and a model.

Everything about *which* provider and *which* model is configuration, not code:
`core/config.py` holds the vocabulary (a key, an endpoint, a default model per
provider) and this module turns the chosen one into a PydanticAI client. Nothing
else in the codebase knows a vendor's name.

Both SDKs are declared dependencies, so they are imported outright: an absent SDK
is a broken install, and failing at import says so loudly. That is a different
thing from the *layer being unusable*, which is about the key and is answered by
`agent.client.available`.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from core.config import Provider, chosen_provider, model_name, provider_names

__all__ = ["chosen_provider", "make_model", "model_name", "provider_names"]


def _openrouter(chosen: Provider) -> OpenRouterProvider:
    """OpenRouter's client, pointed at the configured endpoint when there is one.

    The SDK takes no `base_url`; a gateway is reached by handing it an OpenAI
    client that already knows where to go.
    """
    if not chosen.base_url:
        return OpenRouterProvider(api_key=chosen.api_key)
    from openai import AsyncOpenAI

    # The vendored stub types this parameter as `None`; at runtime it takes the
    # client that carries the endpoint, which is the whole point of setting one.
    client = AsyncOpenAI(api_key=chosen.api_key, base_url=chosen.base_url)
    return OpenRouterProvider(api_key=chosen.api_key, openai_client=client)  # type: ignore[arg-type]


def make_model() -> Any:
    """Build the model for the configured provider. Tests replace this with a fake."""
    chosen = chosen_provider()
    if not chosen.api_key:
        # Checked earlier by `agent.client.available`; this is the loud fallback.
        raise RuntimeError(f"no API key for {chosen.name}")
    name = model_name()
    if chosen.name == "anthropic":
        return AnthropicModel(
            name,
            provider=AnthropicProvider(api_key=chosen.api_key, base_url=chosen.base_url),
        )
    return OpenRouterModel(name, provider=_openrouter(chosen))

"""The single place that names a provider and a model.

Everything about *which* provider and *which* model is configuration, not code:
`core/config.py` holds the vocabulary (a key, an endpoint, a default model per
provider) and this module turns the chosen one into a PydanticAI client. Nothing
else in the codebase knows a vendor's name.

Every entry in that vocabulary speaks the OpenAI chat format, which is what makes
OpenRouter, a self-hosted gateway in front of it, and OpenAI itself the same
client with a different address. So there is one client built here and no vendor
SDK imported for a vendor's sake.

The OpenAI SDK is a declared dependency, so it is imported outright: an absent
SDK is a broken install, and failing at import says so loudly. That is a
different thing from the *layer being unusable*, which is about the key and is
answered by `agent.client.available`.
"""

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
    """Build the model for the configured provider. Tests replace this with a fake.

    `tool_choice` is deliberately not touched. This agent's plan is handed in by a
    call — the terminal tool is the only output — so the OpenAI shape has PydanticAI
    send `"required"`, and a model that refuses forced tool use is not a usable
    `LLM_MODEL`. Gateway thinking modes do refuse it, with a 400; the model named in
    the deployment must be one that does not. See `.env.example`.
    """
    chosen = chosen_provider()
    if not chosen.api_key:
        # Checked earlier by `agent.client.available`; this is the loud fallback.
        raise RuntimeError(f"no API key for {chosen.name}")
    return OpenAIChatModel(model_name(), provider=_provider(chosen))

"""The single place that names a provider and a model.

The OpenRouter SDK is a declared dependency, so it is imported outright: an
absent SDK is a broken install, and failing at import says so loudly. That is a
different thing from the *layer being unusable*, which is about the key and is
answered by `agent.client.available`.
"""

from __future__ import annotations

import os
from typing import Any

from core.config import openrouter_api_key

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"


def model_name() -> str:
    return os.environ.get("AGENT_INTERPRET_MODEL") or DEFAULT_MODEL


def make_model() -> Any:
    """Build the OpenRouter model. Tests replace this with a fake model."""
    from pydantic_ai.models.openrouter import OpenRouterModel
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    key = openrouter_api_key()
    if not key:
        raise RuntimeError("no OPENROUTER_API_KEY")  # pragma: no cover — checked earlier
    return OpenRouterModel(model_name(), provider=OpenRouterProvider(api_key=key))

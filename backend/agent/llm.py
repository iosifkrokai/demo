"""The single place that names a provider and a model.

The OpenRouter SDK import lives here too, so every other module can treat
"the model layer is unavailable" as one fact.
"""

from __future__ import annotations

import os
from typing import Any

from core.config import openrouter_api_key

# pyright: reportPossiblyUnboundVariable=false

try:  # pragma: no cover — the branch taken depends on the deployment
    import pydantic_ai
    from pydantic_ai import RunContext, UsageLimits
    from pydantic_ai.settings import ModelSettings

    SDK_IMPORT_ERROR: str | None = None
except ImportError as exc:  # pragma: no cover
    pydantic_ai = None  # type: ignore[assignment]
    RunContext = None  # type: ignore[assignment,misc]
    UsageLimits = None  # type: ignore[assignment,misc]
    ModelSettings = None  # type: ignore[assignment,misc]
    SDK_IMPORT_ERROR = str(exc)

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"

__all__ = [
    "DEFAULT_MODEL",
    "SDK_IMPORT_ERROR",
    "ModelSettings",
    "RunContext",
    "UsageLimits",
    "make_model",
    "model_name",
    "pydantic_ai",
]


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

"""The interpretation agent's public entry point.

``available()`` answers "is the agent configured"; ``interpret_with_agent`` is
the only way in. Everything else in the package is an implementation detail.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent import llm, mapping, runner
from agent.llm import DEFAULT_MODEL, pydantic_ai
from agent.models import ReaderBrief, TripRequirements
from agent.schema import InterpretDeps
from core.config import openrouter_api_key

if TYPE_CHECKING:
    from db.store.registry import Repositories

log = logging.getLogger(__name__)

__all__ = ["DEFAULT_MODEL", "available", "interpret_with_agent"]


def available() -> bool:
    """True when this layer could run: a key, a model name, an importable SDK.

    Cheap and offline — it answers "is the agent configured", not "is OpenRouter up".
    """
    if pydantic_ai is None:
        return False
    if not openrouter_api_key():
        return False
    return bool(llm.model_name())


def interpret_with_agent(
    query: str,
    brief: ReaderBrief,
    *,
    repos: Repositories | None = None,
    wall_clock_s: float | None = None,
) -> TripRequirements | None:
    """Returns a complete ``TripRequirements`` or ``None``.

    ``None`` is never a half-filled contract: it means there is no reading at all,
    and the caller refuses the request rather than guessing at the query.
    """
    if not isinstance(query, str) or not query.strip():
        return None
    if not available():
        log.info("agent_interpret: agent unavailable (no key/model) — no reader")
        runner._record_model_failure("agent_unavailable", runner._build_prompt(query, brief))
        return None

    deps = InterpretDeps(repos=repos)
    try:
        reading, failure = runner._run_agent(query.strip(), brief, deps, wall_clock_s=wall_clock_s)
    except Exception as exc:
        log.warning("agent_interpret: unexpected failure: %s: %s", type(exc).__name__, exc)
        return None
    if reading is None:
        log.info("agent_interpret: no usable answer (%s) — no reader", failure)
        return None
    return mapping._merge(query.strip(), brief, reading, deps.observed_ids)

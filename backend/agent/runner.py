"""Running the planning agent: limits, timeouts, wall-clock, failure recording."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any

import pydantic_ai
from pydantic_ai import RunContext, ToolOutput, UsageLimits
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.settings import ModelSettings

from agent import llm as model_mod
from agent.models import ReaderBrief
from agent.prompts import compose_instructions
from agent.prompts.notes import _request_note, _ui_note
from agent.schema import PlanningDeps, PlanSubmission
from agent.tools import register_all
from core import constants

log = logging.getLogger(__name__)

MAX_REQUESTS = 10
MAX_OUTPUT_TOKENS = 8192
WALL_CLOCK_TIMEOUT_S = 150.0
MODEL_TIMEOUT_S = 90.0

TIME_NUDGE_LEFT_S = 25.0
SUBMIT_NUDGE = (
    "Time is nearly up. Submit this turn with what you already have — call "
    "submit_plan with the stops you have found. Do not start a new search."
)

_NO_MODEL = {"agent_unavailable", "model_unavailable"}

SUBMIT_DESCRIPTION = (
    "Submit the finished plan. Call this exactly once, at the very end, when the "
    "stops are chosen and measured."
)


def effective_wall_clock(wall_clock_s: float | None) -> float:
    """The wall-clock bound one run gets: the tighter of the fixed ceiling and
    the caller's request budget. ``None`` (or a non-positive value) means the
    ceiling. One function so the deadline the tools see and the timeout the run
    is killed by cannot disagree.
    """
    if wall_clock_s is not None and wall_clock_s > 0:
        return min(WALL_CLOCK_TIMEOUT_S, float(wall_clock_s))
    return WALL_CLOCK_TIMEOUT_S


def _submission_nudge(messages: list[Any], seconds_left: float) -> list[Any]:
    """Append a "submit now" reminder once time is short."""
    if seconds_left > TIME_NUDGE_LEFT_S:
        return messages
    if messages and isinstance(messages[-1], ModelRequest):
        parts = list(messages[-1].parts)
        if any(isinstance(p, UserPromptPart) and p.content == SUBMIT_NUDGE for p in parts):
            return messages
        return [*messages[:-1], ModelRequest([*parts, UserPromptPart(SUBMIT_NUDGE)])]
    return [*messages, ModelRequest([UserPromptPart(SUBMIT_NUDGE)])]


def _time_capability() -> ProcessHistory[PlanningDeps]:
    """The in-loop degradation: a history processor that reads the live deadline."""

    def processor(ctx: RunContext[PlanningDeps], messages: list[Any]) -> list[Any]:
        return _submission_nudge(messages, ctx.deps.seconds_left())

    return ProcessHistory(processor)


def _build_prompt(query: str, brief: Any, *, repair: str | None = None) -> str:
    """The text handed to the model, built in one place."""
    prompt = (
        f"locale={brief.locale}\n{_request_note(brief)}\nrequest={query!r}\n"
        "Plan the walk for this request, then submit it with submit_plan."
    )
    return prompt if repair is None else f"{prompt}\n{repair}"


def _record_model_failure(reason: str, prompt: str = "") -> None:
    """Say why no plan was recorded. Logfire keeps the log with the rest of the run."""
    level = logging.INFO if reason in _NO_MODEL else logging.ERROR
    log.log(level, "agent_plan: no plan (%s) prompt=%r", reason, prompt[:200])


def _catalogue(deps: PlanningDeps) -> Sequence[Any]:
    """The taxonomy as prompt data, or nothing when there is no database."""
    return deps.repos.taxonomy.all() if deps.repos is not None else ()


def _territories(deps: PlanningDeps) -> Sequence[Any]:
    """The named territories as prompt data, or nothing when there is no database."""
    return deps.repos.areas.list_all() if deps.repos is not None else ()


def _build_agent(model: Any, brief: ReaderBrief, deps: PlanningDeps) -> Any:
    """Create the PydanticAI agent, its tool surface, and its terminal output."""
    agent = pydantic_ai.Agent(
        model,
        output_type=ToolOutput(PlanSubmission, name="submit_plan", description=SUBMIT_DESCRIPTION),
        deps_type=PlanningDeps,
        instructions=compose_instructions(_catalogue(deps), _territories(deps), _ui_note(brief)),
        capabilities=[_time_capability()],
    )

    def _remember(ctx: Any, out: dict[str, Any]) -> dict[str, Any]:
        """Record the ids a tool actually returned (the id guard)."""
        for row in out.get("results") or ():
            pid = row.get("id") if isinstance(row, dict) else None
            if isinstance(pid, int):
                ctx.deps.observed_ids.add(pid)
        return out

    register_all(agent, _remember)
    return agent


TOOL_BUDGET_FAILURE = "UsageLimitExceeded"


def _run_with_timeout(fn: Any, timeout_s: float) -> tuple[Any, str | None]:
    """Run `fn()` with a wall-clock bound. Returns (result, failure_reason)."""
    ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-plan")
    try:
        future = ex.submit(fn)
        try:
            return future.result(timeout=timeout_s), None
        except FuturesTimeout:
            future.cancel()
            return None, "timeout"
        except Exception as exc:
            log.warning("agent_plan: run failed: %s: %s", type(exc).__name__, exc)
            return None, type(exc).__name__
    finally:
        ex.shutdown(wait=False)


def _run_agent(
    query: str,
    brief: ReaderBrief,
    deps: PlanningDeps,
    wall_clock_s: float | None = None,
    *,
    repair: str | None = None,
) -> tuple[PlanSubmission | None, str | None]:
    """Run one bounded planning pass. Returns (submission, failure_reason)."""
    try:
        model = model_mod.make_model()
    except Exception as exc:
        log.info("agent_plan: model unavailable: %s", exc)
        return None, "model_unavailable"

    usage_limits = UsageLimits(
        request_limit=MAX_REQUESTS,
        tool_calls_limit=constants.MAX_TOOL_CALLS,
    )
    model_settings = ModelSettings(max_tokens=MAX_OUTPUT_TOKENS, timeout=MODEL_TIMEOUT_S)
    prompt = _build_prompt(query, brief)

    def call() -> Any:
        agent = _build_agent(model, brief, deps)
        return agent.run_sync(
            _build_prompt(query, brief, repair=repair),
            deps=deps,
            usage_limits=usage_limits,
            model_settings=model_settings,
        )

    bound = effective_wall_clock(wall_clock_s)
    result, failure = _run_with_timeout(call, bound)
    if failure is not None:
        _record_model_failure(failure, prompt)
        return None, failure
    if result is None:
        _record_model_failure("unexpected_output", prompt)
        return None, "unexpected_output"
    if not isinstance(getattr(result, "output", None), PlanSubmission):
        _record_model_failure("no_submission", prompt)
        return None, "no_submission"
    return result.output, None


__all__ = [
    "_build_prompt",
    "_record_model_failure",
    "_run_agent",
    "_run_with_timeout",
    "effective_wall_clock",
]

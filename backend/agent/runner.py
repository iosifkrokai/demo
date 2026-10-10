"""Running the interpretation agent: limits, timeouts, wall-clock, failure recording."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any

from agent import model as model_mod, telemetry
from agent.model import SDK_IMPORT_ERROR, ModelSettings, UsageLimits, pydantic_ai
from agent.prompts import compose_instructions
from agent.prompts.notes import _request_note, _ui_note
from agent.schema import AgentReading, InterpretDeps
from agent.tools import register_all
from contracts.planner import GenerateReq
from telemetry import trace

log = logging.getLogger(__name__)

MAX_TOOL_CALLS = 24
MAX_REQUESTS = 12
MAX_OUTPUT_TOKENS = 8192
WALL_CLOCK_TIMEOUT_S = 120.0
MODEL_TIMEOUT_S = 90.0

_NO_MODEL = {"agent_unavailable", "pydantic_ai_unavailable", "model_unavailable"}


def _build_prompt(query: str, req: Any) -> str:
    """The text handed to the model, built in one place."""
    return (
        f"locale={req.locale}\n{_request_note(req)}\nrequest={query!r}\n"
        "Return the requirement list for this request."
    )


def _record_model_failure(reason: str, prompt: str = "") -> None:
    """Say in the trace why no model reading was recorded."""
    trace.record(
        "interpret · model",
        "skipped" if reason in _NO_MODEL else "error",
        input=prompt or None,
        reason=reason,
    )


def _build_agent(model: Any, req: GenerateReq) -> Any:
    """Create the PydanticAI agent and register the bounded tool surface."""
    agent = pydantic_ai.Agent(
        model,
        output_type=AgentReading,
        deps_type=InterpretDeps,
        instructions=compose_instructions(_ui_note(req)),
    )

    def _remember(ctx: Any, out: dict) -> dict:
        """Record the ids a tool actually returned (the place_id guard)."""
        for row in out.get("results") or ():
            pid = row.get("id") if isinstance(row, dict) else None
            if isinstance(pid, int):
                ctx.deps.observed_ids.add(pid)
        return out

    register_all(agent, _remember)
    return agent


def _run_with_timeout(fn: Any, timeout_s: float) -> tuple[Any, str | None]:
    """Run `fn()` with a wall-clock bound. Returns (result, failure_reason)."""
    ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-interpret")
    try:
        future = ex.submit(fn)
        try:
            return future.result(timeout=timeout_s), None
        except FuturesTimeout:
            future.cancel()
            return None, "timeout"
        except Exception as exc:
            log.warning("agent_interpret: run failed: %s: %s", type(exc).__name__, exc)
            return None, type(exc).__name__
    finally:
        ex.shutdown(wait=False)


def _run_agent(
    query: str, req: GenerateReq, deps: InterpretDeps, wall_clock_s: float | None = None
) -> tuple[AgentReading | None, str | None]:
    """Run one bounded interpretation. Returns (reading, failure_reason)."""
    if pydantic_ai is None:
        log.info(
            "agent_interpret: pydantic-ai not importable (%s) — no reader",
            SDK_IMPORT_ERROR,
        )
        return None, "pydantic_ai_unavailable"

    try:
        model = model_mod.make_model()
    except Exception as exc:
        log.info("agent_interpret: model unavailable: %s", exc)
        return None, "model_unavailable"

    usage_limits = UsageLimits(
        request_limit=MAX_REQUESTS,
        tool_calls_limit=MAX_TOOL_CALLS,
    )
    model_settings = ModelSettings(max_tokens=MAX_OUTPUT_TOKENS, timeout=MODEL_TIMEOUT_S)
    prompt = _build_prompt(query, req)

    def call() -> Any:
        agent = _build_agent(model, req)
        return agent.run_sync(
            prompt, deps=deps, usage_limits=usage_limits, model_settings=model_settings
        )

    bound = WALL_CLOCK_TIMEOUT_S
    if wall_clock_s is not None and wall_clock_s > 0:
        bound = min(bound, float(wall_clock_s))
    result, failure = _run_with_timeout(call, bound)
    if failure is not None:
        _record_model_failure(failure, prompt)
        return None, failure
    if result is None:
        _record_model_failure("unexpected_output", prompt)
        return None, "unexpected_output"
    telemetry._record_model_call(req, prompt, result)
    if not isinstance(getattr(result, "output", None), AgentReading):
        return None, "unexpected_output"
    return result.output, None


# agent.client reuses the failure path and the prompt builder.
__all__ = ["_build_prompt", "_record_model_failure", "_run_agent", "_run_with_timeout"]

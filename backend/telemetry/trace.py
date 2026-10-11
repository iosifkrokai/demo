"""Structured trace of one route request, held in memory for its lifetime.

Spans record what a step did; an unknown id is ``None``, not an empty trace.

This module survives only as long as `planner/` does — it is the recorder the
pipeline's steps call, and it will be deleted with them. Observation now belongs
to Logfire, which instruments the agent loop without any of this.
"""

from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass, field
from typing import Any

TTL_S = 300.0

_current: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)


@dataclass
class Span:
    name: str
    started_at: float
    ended_at: float
    status: str = "ok"
    facts: dict[str, Any] = field(default_factory=dict)
    input: Any = None
    kind: str = "span"
    output: Any = None
    model: str | None = None
    usage: dict[str, int] | None = None


@dataclass
class _Trace:
    spans: list[Span] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    wall_started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.monotonic)
    session_id: str | None = None


_traces: dict[str, _Trace] = {}


def begin(trace_id: str | None, session_id: str | None = None) -> str | None:
    """Start tracing ``trace_id`` — called by the API, not by the pipeline.

    Returns the id in use, or ``None`` when the client asked for no trace.
    """
    if not trace_id:
        _current.set(None)
        return None
    _prune()
    _traces[trace_id] = _Trace(session_id=session_id)
    _current.set(trace_id)
    return trace_id


def record(
    name: str,
    status: str = "ok",
    *,
    input: Any = None,
    output: Any = None,
    kind: str = "span",
    model: str | None = None,
    usage: dict[str, int] | None = None,
    **facts: Any,
) -> None:
    """Append one span for the current request.

    The span is contiguous; ``status`` is the step's own fate, not a verdict.
    """
    trace_id = _current.get()
    if not trace_id:
        return
    trace = _traces.get(trace_id)
    if trace is None:
        return
    now = time.monotonic()
    started_at = trace.spans[-1].ended_at if trace.spans else trace.started_at
    trace.spans.append(
        Span(
            name=name,
            started_at=started_at,
            ended_at=now,
            status=status,
            facts=dict(facts),
            input=input,
            kind=kind,
            output=output,
            model=model,
            usage=usage,
        )
    )
    trace.updated_at = now


def finish() -> None:
    """The request is over: stop tracking it."""
    trace_id = _current.get()
    if not trace_id:
        return
    trace = _traces.get(trace_id)
    if trace is not None:
        trace.updated_at = time.monotonic()
    _current.set(None)


def get(trace_id: str) -> dict[str, Any] | None:
    """The spans of one run, or ``None`` when the id is unknown.

    Millisecond offsets are relative to the trace start, never wall clocks.
    """
    trace = _traces.get(trace_id)
    if trace is None:
        return None
    start = trace.started_at
    spans = [
        {
            "name": span.name,
            "started_ms": int((span.started_at - start) * 1000),
            "duration_ms": int((span.ended_at - span.started_at) * 1000),
            "status": span.status,
            "facts": span.facts,
            "kind": span.kind,
            "model": span.model,
            "usage": span.usage,
        }
        for span in trace.spans
    ]
    return {
        "trace_id": trace_id,
        "session_id": trace.session_id,
        "elapsed_ms": int((time.monotonic() - start) * 1000),
        "spans": spans,
    }


def _prune() -> None:
    now = time.monotonic()
    for key, trace in list(_traces.items()):
        if now - trace.updated_at > TTL_S:
            _traces.pop(key, None)

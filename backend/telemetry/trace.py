"""Structured trace of one route request, exported to a self-hosted Langfuse.

Spans record what a step did; an unknown id is ``None``, not an empty trace.
"""

from __future__ import annotations

import contextvars
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from core.config import langfuse_configured

log = logging.getLogger(__name__)

TTL_S = 300.0

_current: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_id", default=None
)


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

_UNSET = object()
_langfuse_state: dict[str, Any] = {"client": _UNSET}


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
    """The request is over: export the spans to Langfuse, then stop tracking.

    The export is best-effort and never raised into the request.
    """
    trace_id = _current.get()
    if not trace_id:
        return
    _export_to_langfuse(trace_id)
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


_LEVEL = {"ok": "DEFAULT", "skipped": "DEBUG", "error": "ERROR"}


def _observation(span: Span) -> dict[str, Any]:
    """How one span is handed to Langfuse.

    A step becomes a span; a model call becomes a generation.
    """
    level = _LEVEL.get(span.status, "DEFAULT")
    if span.kind == "generation":
        return {
            "name": span.name,
            "as_type": "generation",
            "input": span.input,
            "output": span.output,
            "model": span.model,
            "usage_details": span.usage,
            "metadata": span.facts or None,
            "level": level,
        }
    prose = span.output is not None
    return {
        "name": span.name,
        "as_type": "span",
        "input": span.input,
        "output": span.output if prose else (span.facts or None),
        "metadata": (span.facts or None) if prose else None,
        "level": level,
    }


def _client():
    if _langfuse_state["client"] is _UNSET:
        if langfuse_configured():
            from langfuse import Langfuse

            _langfuse_state["client"] = Langfuse()
        else:
            _langfuse_state["client"] = None
    return _langfuse_state["client"]


def _export_to_langfuse(trace_id: str) -> None:
    """Replay one collected trace into Langfuse, or do nothing.

    Span start/end times are backdated so the UI shows the real durations.
    """
    client = _client()
    if client is None:
        return
    trace = _traces.get(trace_id)
    if trace is None or not trace.spans:
        return
    try:
        first = trace.spans[0]
        last = trace.spans[-1]
        start_ms = int(trace.wall_started_at * 1000)

        def abs_ms(offset_s: float) -> int:
            return start_ms + round((offset_s - trace.started_at) * 1000)

        schedule: list[tuple[int, int]] = []
        previous_ms = start_ms
        for span in trace.spans:
            span_start_ms = max(abs_ms(span.started_at), previous_ms + 1)
            span_end_ms = max(abs_ms(span.ended_at), span_start_ms + 1)
            schedule.append((span_start_ms, span_end_ms))
            previous_ms = span_start_ms

        with _propagation(trace.session_id):
            root = client.start_observation(
                name="route",
                as_type="span",
                input=first.facts or None,
                output=last.facts or None,
            )
            for span, (span_start_ms, span_end_ms) in zip(trace.spans, schedule, strict=False):
                obs = root.start_observation(**_observation(span))
                _backdate(obs, span_start_ms * 1_000_000)
                obs.end(end_time=span_end_ms * 1_000_000)
        _backdate(root, start_ms * 1_000_000)
        root.end(end_time=schedule[-1][1] * 1_000_000)
    except Exception:
        log.warning("langfuse export failed for %s", trace_id, exc_info=True)


@contextmanager
def _propagation(session_id: str | None):
    """Group this trace into a Langfuse session, or a no-op when unnamed."""
    if not session_id:
        yield
        return
    from langfuse import propagate_attributes

    with propagate_attributes(session_id=session_id):
        yield


def _backdate(obs: Any, start_ns: int) -> None:
    """Set an observation's start time, which the SDK does not expose publicly.

    Guarded on the attribute name, so an SDK rename degrades to "now".
    """
    otel = getattr(obs, "_otel_span", None)
    if otel is not None and hasattr(otel, "_start_time"):
        otel._start_time = start_ns


def shutdown() -> None:
    """Flush any queued spans on process exit; a no-op when unconfigured."""
    client = _langfuse_state["client"]
    if client is _UNSET or client is None:
        return
    try:
        client.flush()
    except Exception:
        log.warning("langfuse shutdown flush failed", exc_info=True)

"""Structured trace of one route request, exported to a self-hosted Langfuse.

`progress` answers «how far has the request got» in eight coarse stages; `trace`
answers «what happened at each node», as spans. The same honesty rules as
`progress`:

* a span records what a step *did* — the branch taken, the counts, the timing —
  never what it might do;
* an unknown id is ``None``, not an empty trace: the caller must be able to tell
  «этого запуска нет» from «запуск ещё ничего не сделал»;
* with no id nothing is tracked and nothing changes (benchmarks, the golden
  harness, the CLI).

A step that asks a model also records the conversation: ``kind="generation"``
carries the prompt as ``input``, the raw answer, the model name and the tokens
spent, so Langfuse can show what the model was actually asked instead of only the
conclusion drawn from it (see ``agent_interpret._record_model_call``).

Spans are contiguous: each span starts where the previous one ended, so one run
is an unbroken timeline and a viewer can draw a single bar. This is a debugging
aid, not a profiler — a gap between two ``record`` calls is attributed to the
step that follows it. The order is preserved into Langfuse even for steps faster
than the millisecond its timestamps are stored at, so a viewer reads the run in
the order it happened.

When Langfuse is configured (``LANGFUSE_PUBLIC_KEY`` + ``LANGFUSE_SECRET_KEY``),
``finish()`` replays the collected spans into it as one trace with a child
observation per span, backdating the OpenTelemetry start/end times so the
timeline the UI draws is the real one. A ``session_id`` passed to ``begin()``
groups the traces of one guide run together in the UI. The export is best-effort:
a Langfuse that is down or half-configured never costs a request, and the SDK's
batch exporter retries on a background thread so the agent does not block on it.

One uvicorn worker in a demo, so a process-local dict is enough, exactly like
``progress``.
"""

from __future__ import annotations

import contextvars
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from .config import langfuse_configured

log = logging.getLogger(__name__)

#: A trace is dropped this long after its last update.
TTL_S = 300.0

#: Bound to the request's own id, in the thread that runs the pipeline.
_current: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_id", default=None
)


@dataclass
class Span:
    name: str
    started_at: float
    ended_at: float
    status: str = "ok"  # "ok" | "skipped" | "error"
    facts: dict[str, Any] = field(default_factory=dict)
    # What went *into* the step, when that is worth showing next to what came out.
    # ``None`` means "not recorded" — most deterministic steps only report their
    # result, while a model call must report its prompt, or the trace claims a
    # decision was made without saying what from.
    input: Any = None
    # "span" is a step; "generation" is a call to a model, which Langfuse shows in
    # its own panel — prompt, answer, model, tokens, cost.
    kind: str = "span"
    # A model call's raw answer. ``facts`` stays the small honest numbers either
    # way, so a generation reports both the text it got back and what it spent.
    output: Any = None
    model: str | None = None
    usage: dict[str, int] | None = None


@dataclass
class _Trace:
    spans: list[Span] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    # Wall clock, so the exporter can convert relative offsets into the absolute
    # timestamps Langfuse expects. Monotonic alone cannot be mapped to epoch.
    wall_started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.monotonic)
    # The guide run this request belongs to, when the client named one. Requests
    # sharing it group into one Langfuse session («полный прогон») rather than
    # scattering across the trace list; ``None`` means the trace stands alone.
    session_id: str | None = None


_traces: dict[str, _Trace] = {}

#: The Langfuse client, built once on first use and never when unconfigured.
#: ``None`` means "not configured"; ``_UNSET`` means "not decided yet".
_UNSET = object()
_langfuse_state: dict[str, Any] = {"client": _UNSET}


def begin(trace_id: str | None, session_id: str | None = None) -> str | None:
    """Start tracing ``trace_id`` — called by the API, not by the pipeline.

    Returns the id in use, or ``None`` when the client asked for no trace: then
    every ``record()`` below is a no-op and costs nothing.

    ``session_id`` names the guide run this request belongs to; requests sharing
    it become one session in Langfuse. It is optional — without it the trace is
    exported on its own, exactly as before.
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

    The span starts where the previous one ended, so the timeline is contiguous;
    ``facts`` are the small, honest numbers the step already computes for its
    own log line (candidate counts, the branch taken, whether it was skipped).

    ``status`` is the step's own fate — ``ok`` / ``skipped`` / ``error``, which
    Langfuse reads as a level. A verdict the *domain* produced (``ready``,
    ``infeasible``) is a fact like any other and needs its own name: passing it
    as ``status`` would swallow it into the level and lose it from the trace.

    ``input``/``output``/``kind``/``model``/``usage`` describe a *model call*
    when a step makes one: the prompt, the answer, the model's name and the
    tokens. A plain step leaves them alone and stays a span.
    """
    trace_id = _current.get()
    if not trace_id:
        return
    trace = _traces.get(trace_id)
    if trace is None:  # TTL ran out under a long request — nothing to record
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

    The export happens before the id is cleared so the exporter still has it;
    it is best-effort and never raised into the request.
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

    Millisecond offsets are relative to the trace start so the client never has
    to align wall clocks. A model call reports its model and tokens here but not
    its prompt and answer: a prompt is thousands of characters of prose, and the
    reader who wants it wants Langfuse's generation panel, not a step list.
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


# Langfuse export

_LEVEL = {"ok": "DEFAULT", "skipped": "DEBUG", "error": "ERROR"}


def _observation(span: Span) -> dict[str, Any]:
    """How one span is handed to Langfuse.

    A step becomes a span; a model call becomes a *generation*, the type
    Langfuse gives its own panel — prompt, answer, model, tokens, cost. What
    went in travels as ``input``. What came out is the step's own report: its
    facts, or the text it produced when it produced text — and then the facts
    ride along as metadata, so the counts are not hidden behind the prose.
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
            from langfuse import Langfuse  # imported only when actually used

            _langfuse_state["client"] = Langfuse()  # reads LANGFUSE_HOST/_PUBLIC_KEY/_SECRET_KEY
        else:
            _langfuse_state["client"] = None
    return _langfuse_state["client"]


def _export_to_langfuse(trace_id: str) -> None:
    """Replay one collected trace into Langfuse, or do nothing.

    One trace with a child observation per span. Absolute timestamps are
    reconstructed from the wall-clock start plus the contiguous offsets, and the
    OpenTelemetry span start/end times are set explicitly so the UI timeline
    shows the real durations rather than the instant this replay runs.
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

        # Langfuse keeps times at millisecond resolution, and most steps here are
        # faster than that: ``preprocess`` computes in microseconds, so it lands
        # on the very same millisecond as the 13-second model call it preceded,
        # and the UI is then free to draw them in either order — it showed the
        # step that ran *before* the call as if it ran after it. Walking the
        # spans in recorded order and nudging each past the one before it buys a
        # readable timeline for at most a millisecond per sub-millisecond step.
        schedule: list[tuple[int, int]] = []
        previous_ms = start_ms
        for span in trace.spans:
            span_start_ms = max(abs_ms(span.started_at), previous_ms + 1)
            span_end_ms = max(abs_ms(span.ended_at), span_start_ms + 1)
            schedule.append((span_start_ms, span_end_ms))
            previous_ms = span_start_ms

        with _propagation(trace.session_id):
            # Root inside the context too: the session id must sit on the trace's
            # own span, not only on its children.
            root = client.start_observation(
                name="route",
                as_type="span",
                input=first.facts or None,
                output=last.facts or None,
            )
            for span, (span_start_ms, span_end_ms) in zip(trace.spans, schedule):
                obs = root.start_observation(**_observation(span))
                _backdate(obs, span_start_ms * 1_000_000)
                obs.end(end_time=span_end_ms * 1_000_000)
        _backdate(root, start_ms * 1_000_000)
        root.end(end_time=schedule[-1][1] * 1_000_000)
    except Exception:
        # Tracing must never take the request down: a Langfuse bug, a schema
        # change, or a down host all leave the answer untouched.
        log.warning("langfuse export failed for %s", trace_id, exc_info=True)


@contextmanager
def _propagation(session_id: str | None):
    """Group this trace into a Langfuse session, or a no-op when unnamed.

    ``propagate_attributes`` sets the trace-level ``session_id`` on every span
    started inside the context (the SDK's span processor reads it from the OTel
    context), which is what makes several requests show up as one «полный
    прогон» in the UI instead of separate traces. Imported lazily so a
    Langfuse-less run never pays for the SDK.
    """
    if not session_id:
        yield
        return
    from langfuse import propagate_attributes

    with propagate_attributes(session_id=session_id):
        yield


def _backdate(obs: Any, start_ns: int) -> None:
    """Set an observation's start time, which the SDK does not expose publicly.

    The OpenTelemetry span records its start at creation; the exporter replays a
    finished timeline, so it must be moved back. Guarded on the attribute name,
    so a future SDK rename degrades to "now" instead of raising.
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

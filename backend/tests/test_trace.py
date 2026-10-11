"""Trace is a fact about the pipeline, told in spans — Langfuse-style."""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import itertools

from api.routers.routes import route_trace
from telemetry import trace


@pytest.fixture(autouse=True)
def _clean():
    trace._traces.clear()
    trace._current.set(None)
    yield
    trace._traces.clear()
    trace._current.set(None)


def test_spans_carry_the_facts_a_step_recorded():
    trace.begin("job-1")
    trace.record("interpret", source="agent", result_mode="route")
    trace.record("retrieve", candidates=17)

    data = trace.get("job-1")
    assert data is not None
    names = [s["name"] for s in data["spans"]]
    assert names == ["interpret", "retrieve"]
    assert data["spans"][0]["facts"] == {"source": "agent", "result_mode": "route"}
    assert data["spans"][1]["facts"] == {"candidates": 17}


def test_spans_are_contiguous_so_one_run_is_one_timeline():
    trace.begin("job-2")
    trace.record("preprocess")
    trace.record("interpret")
    data = trace.get("job-2")
    assert data is not None
    first, second = data["spans"]
    assert first["started_ms"] == 0
    assert second["started_ms"] == first["started_ms"] + first["duration_ms"]


def test_an_unknown_id_is_unknown_not_an_empty_trace():
    assert trace.get("never-started") is None

    with pytest.raises(HTTPException) as raised:
        route_trace("never-started")
    assert raised.value.status_code == 404
    assert raised.value.detail == {"reason": "unknown_trace_id"}


def test_without_an_id_nothing_is_tracked_and_nothing_breaks():
    assert trace.begin(None) is None
    trace.record("interpret")
    trace.record("retrieve")
    trace.finish()
    assert trace._traces == {}


def test_a_trace_is_dropped_once_it_is_stale():
    trace.begin("job-3")
    tracked = trace._traces["job-3"]
    tracked.updated_at = time.monotonic() - trace.TTL_S - 1

    trace.begin("job-4")

    assert trace.get("job-3") is None
    assert trace.get("job-4") is not None


def test_elapsed_ms_is_monotonic_and_non_negative():
    trace.begin("job-5")
    trace.record("query")
    data = trace.get("job-5")
    assert data is not None
    assert data["elapsed_ms"] >= 0
    assert data["spans"][0]["duration_ms"] >= 0


def test_the_run_id_is_carried_and_reported():
    trace.begin("job-s1", session_id="run-7")
    trace.record("interpret")

    data = trace.get("job-s1")
    assert data is not None
    assert data["session_id"] == "run-7"


def test_without_a_run_id_the_trace_stands_alone():
    trace.begin("job-s2")
    data = trace.get("job-s2")
    assert data is not None
    assert data["session_id"] is None


class _FakeObs:
    """The little of the SDK the exporter touches: create children, then end."""

    def start_observation(self, **_kwargs: object) -> _FakeObs:
        return _FakeObs()

    def end(self, **_kwargs: object) -> None:
        return None


class _FakeClient:
    def start_observation(self, **_kwargs: object) -> _FakeObs:
        return _FakeObs()


def _export_spy(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Capture the session id the exporter opens its propagation context with."""
    seen: list[str | None] = []

    @contextmanager
    def spy(session_id: str | None):
        seen.append(session_id)
        yield

    monkeypatch.setattr(trace, "_client", _FakeClient)
    monkeypatch.setattr(trace, "_propagation", spy)
    return seen


def test_export_opens_the_session_the_trace_was_begun_with(monkeypatch):
    seen = _export_spy(monkeypatch)
    trace.begin("job-s3", session_id="run-9")
    trace.record("interpret")
    trace.finish()

    assert seen == ["run-9"]


def test_export_without_a_run_id_opens_no_session(monkeypatch):
    seen = _export_spy(monkeypatch)
    trace.begin("job-s4")
    trace.record("interpret")
    trace.finish()

    assert seen == [None]


class _RecordingObs:
    """A span that remembers how it was created, the way Langfuse would."""

    def __init__(self, calls: list[dict]) -> None:
        self._calls = calls

    def start_observation(self, **kwargs: object) -> _RecordingObs:
        self._calls.append(kwargs)
        return _RecordingObs(self._calls)

    def end(self, **_kwargs: object) -> None:
        return None


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def start_observation(self, **kwargs: object) -> _RecordingObs:
        self.calls.append(kwargs)
        return _RecordingObs(self.calls)


@contextmanager
def _no_propagation(_session_id: str | None):
    yield


def _export_seen(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Every observation the exporter asks Langfuse to create, in order."""
    client = _RecordingClient()
    monkeypatch.setattr(trace, "_client", lambda: client)
    monkeypatch.setattr(trace, "_propagation", _no_propagation)
    return client.calls


def test_a_model_call_keeps_its_model_tokens_and_answer(monkeypatch):
    trace.begin("job-g1")
    trace.record(
        "interpret · model",
        kind="generation",
        input=[{"role": "user", "content": "все костёлы области"}],
        output={"requirements": []},
        model="deepseek/deepseek-v4.1-flash",
        usage={"input": 1200, "output": 80, "total": 1280},
        requests=2,
        tool_calls=1,
    )

    data = trace.get("job-g1")
    assert data is not None
    span = data["spans"][0]
    assert span["kind"] == "generation"
    assert span["model"] == "deepseek/deepseek-v4.1-flash"
    assert span["usage"] == {"input": 1200, "output": 80, "total": 1280}
    assert span["facts"] == {"requests": 2, "tool_calls": 1}
    assert "input" not in span
    assert "output" not in span


def test_export_hands_a_model_call_over_as_a_generation(monkeypatch):
    calls = _export_seen(monkeypatch)
    trace.begin("job-g2")
    trace.record("query", query="все костёлы области")
    trace.record(
        "interpret · model",
        kind="generation",
        input=[{"role": "user", "content": "все костёлы области"}],
        output={"requirements": []},
        model="deepseek/deepseek-v4.1-flash",
        usage={"input": 3, "output": 1, "total": 4},
        requests=1,
    )
    trace.finish()

    root, query, call = calls
    assert root["name"] == "route"
    assert query["as_type"] == "span"
    assert call["as_type"] == "generation"
    assert call["input"] == [{"role": "user", "content": "все костёлы области"}]
    assert call["output"] == {"requirements": []}
    assert call["model"] == "deepseek/deepseek-v4.1-flash"
    assert call["usage_details"] == {"input": 3, "output": 1, "total": 4}
    assert call["metadata"] == {"requests": 1}


def test_a_step_still_reports_its_facts_as_the_output(monkeypatch):
    calls = _export_seen(monkeypatch)
    trace.begin("job-g3")
    trace.record("retrieve", candidates=17)
    trace.finish()

    assert calls[1]["as_type"] == "span"
    assert calls[1]["output"] == {"candidates": 17}
    assert calls[1]["input"] is None
    assert calls[1]["metadata"] is None


def test_a_step_that_produced_text_reports_it_and_keeps_its_numbers(monkeypatch):
    calls = _export_seen(monkeypatch)
    trace.begin("job-g4")
    trace.record(
        "verify",
        plan_status="degraded",
        output=[{"asked": "туалет", "status": "unmet", "reason": "hard_service_absent"}],
    )
    trace.finish()

    verdict = calls[1]
    assert verdict["output"] == [
        {"asked": "туалет", "status": "unmet", "reason": "hard_service_absent"}
    ]
    assert verdict["metadata"] == {"plan_status": "degraded"}
    assert verdict["level"] == "DEFAULT"


def test_a_step_that_did_not_run_is_exported_as_debug(monkeypatch):
    calls = _export_seen(monkeypatch)
    trace.begin("job-g5")
    trace.record("diversity", "skipped", candidates=24, before=24, trimmed=False)
    trace.finish()

    assert calls[1]["level"] == "DEBUG"
    assert calls[1]["output"] == {"candidates": 24, "before": 24, "trimmed": False}


class _OtelSpan:
    """The OpenTelemetry span the exporter backdates through."""

    _start_time = 0


class _TimedObs:
    """A span that remembers the timestamps the exporter gave it."""

    def __init__(self, record: dict, sink: list[dict]) -> None:
        self._record = record
        self._sink = sink
        self._otel_span = _OtelSpan()

    def start_observation(self, **kwargs: object) -> _TimedObs:
        child: dict = {"name": kwargs["name"]}
        self._sink.append(child)
        return _TimedObs(child, self._sink)

    def end(self, *, end_time: int | None = None) -> None:
        self._record["start_ns"] = self._otel_span._start_time
        self._record["end_ns"] = end_time


class _TimedClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def start_observation(self, **kwargs: object) -> _TimedObs:
        root: dict = {"name": kwargs["name"]}
        self.calls.append(root)
        return _TimedObs(root, self.calls)


def test_a_step_that_took_less_than_a_millisecond_still_keeps_its_place(monkeypatch):
    """Langfuse stores milliseconds; a microsecond step must not fall behind."""
    client = _TimedClient()
    monkeypatch.setattr(trace, "_client", lambda: client)
    monkeypatch.setattr(trace, "_propagation", _no_propagation)

    trace.begin("job-ms")
    for name in ("query", "preprocess", "interpret · model", "retrieve"):
        trace.record(name)
    trace.finish()

    placed = [c for c in client.calls if c["name"] != "route"]
    starts = [c["start_ns"] for c in placed]
    assert [c["name"] for c in placed] == [
        "query",
        "preprocess",
        "interpret · model",
        "retrieve",
    ], "the export reordered the run"
    assert len(set(starts)) == len(starts), "two steps share a timestamp"
    assert all(b - a >= 1_000_000 for a, b in itertools.pairwise(starts)), (
        "a step was placed less than a millisecond after its predecessor"
    )
    assert all(c["end_ns"] > c["start_ns"] for c in placed)

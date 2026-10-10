"""Progress is a fact about the pipeline, told in codes."""

from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.main import route_progress
from contracts.planner import GenerateReq
from infra import progress


@pytest.fixture(autouse=True)
def _clean():
    progress._trackers.clear()
    progress._current.set(None)
    yield
    progress._trackers.clear()
    progress._current.set(None)


def test_a_fresh_id_starts_at_the_first_stage():
    progress.begin("job-1")
    snapshot = progress.snapshot("job-1")
    assert snapshot is not None
    assert snapshot["stage"] == progress.STAGE_INTERPRETING
    assert snapshot["done"] is False
    assert snapshot["failed"] is False
    assert isinstance(snapshot["elapsed_ms"], int)


def test_notes_advance_the_stage_in_the_order_the_pipeline_runs_them():
    progress.begin("job-2")
    seen = []
    for stage in progress.ORDER:
        progress.note(stage)
        seen.append(progress.snapshot("job-2")["stage"])
    assert seen == list(progress.ORDER)


def test_an_unknown_id_is_unknown_not_an_empty_stage():
    assert progress.snapshot("never-started") is None

    with pytest.raises(HTTPException) as raised:
        route_progress("never-started")
    assert raised.value.status_code == 404
    assert raised.value.detail == {"reason": "unknown_progress_id"}


def test_the_endpoint_reports_what_the_pipeline_recorded():
    progress.begin("job-3")
    progress.note(progress.STAGE_ORDERING)
    assert route_progress("job-3") == progress.snapshot("job-3")

    progress.finish()
    assert route_progress("job-3")["done"] is True


def test_without_an_id_nothing_is_tracked_and_nothing_breaks():
    assert progress.begin(None) is None
    for stage in progress.ORDER:
        progress.note(stage)
    progress.finish()
    assert progress._trackers == {}


def test_finish_records_failure_so_a_client_is_not_left_watching_a_frozen_stage():
    progress.begin("job-4")
    progress.note(progress.STAGE_SEARCHING)
    progress.finish(failed=True)

    snapshot = progress.snapshot("job-4")
    assert snapshot["done"] is True
    assert snapshot["failed"] is True


def test_a_tracker_is_dropped_once_it_is_stale():
    progress.begin("job-5")
    tracker = progress._trackers["job-5"]
    tracker.updated_at = time.monotonic() - progress.TTL_S - 1

    progress.begin("job-6")

    assert progress.snapshot("job-5") is None
    assert progress.snapshot("job-6") is not None


def test_the_request_accepts_a_progress_id_and_does_not_require_one():
    assert GenerateReq(query="замки Гродно").progress_id is None
    assert GenerateReq(query="замки Гродно", progress_id="abc").progress_id == "abc"

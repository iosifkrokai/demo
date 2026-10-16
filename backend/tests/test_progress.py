"""The run's stage, as the panel watching it reads it."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import routes as routes_api
from core import progress


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes_api.router)
    return TestClient(app)


def test_an_untracked_run_is_unknown() -> None:
    response = _client().get("/routes/progress/nobody-is-watching")

    assert response.status_code == 404
    assert response.json() == {"reason": "unknown_progress_id"}


def test_a_tracked_run_reports_the_stage_it_reached() -> None:
    progress.RUNS.begin("p-tracked")
    progress.RUNS.mark("p-tracked", progress.DRAWING_LINE)

    body = _client().get("/routes/progress/p-tracked").json()

    assert body["stage"] == "drawing_line"
    assert body["done"] is False
    assert body["failed"] is False
    assert body["elapsed_ms"] >= 0


def test_a_finished_run_says_so() -> None:
    progress.RUNS.begin("p-finished")
    progress.RUNS.finish("p-finished")

    body = _client().get("/routes/progress/p-finished").json()

    assert body["stage"] == "done"
    assert body["done"] is True
    assert body["failed"] is False


def test_a_failed_run_says_so() -> None:
    progress.RUNS.begin("p-failed")
    progress.RUNS.finish("p-failed", failed=True)

    assert _client().get("/routes/progress/p-failed").json()["failed"] is True


def test_a_finished_run_is_not_moved_back() -> None:
    runs = progress.Runs()
    runs.begin("p")
    runs.finish("p")

    runs.mark("p", progress.DRAWING_LINE)

    state = runs.read("p")
    assert state is not None
    assert state["stage"] == "done"
    assert state["done"] is True


def test_a_run_that_goes_stale_is_forgotten() -> None:
    runs = progress.Runs(ttl_s=-1)
    runs.begin("p")

    assert runs.read("p") is None


def test_marking_a_run_nobody_began_leaves_it_untracked() -> None:
    runs = progress.Runs()
    runs.mark("p-nobody", progress.DRAWING_LINE)
    runs.finish("p-nobody")

    assert runs.read("p-nobody") is None


def test_a_run_without_an_id_is_not_tracked_at_all() -> None:
    runs = progress.Runs()
    runs.begin(None)
    runs.mark(None, progress.DRAWING_LINE)
    runs.finish(None)

    assert runs.read("None") is None

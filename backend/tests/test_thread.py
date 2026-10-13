"""The server-side thread: the journal, the reconciliation, and the router that wires them."""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import thread
from agent.client import AgentOutcome
from agent.models import BriefStop, ReaderBrief, RefinementBrief
from agent.schema import PlanSubmission
from api.models.routes import ParsedQuery, RouteResponse, RouteSummary
from api.routers import routes as routes_api
from db.models.thread import Thread, Turn
from db.store.errors import StorageUnavailableError
from routing.models import TripRequirements


class FakeThreadRepository:
    """An in-memory stand-in for the journal, with the failure modes switchable."""

    def __init__(self, *, unreadable: bool = False, unwritable: bool = False) -> None:
        self._turns: dict[str, list[Turn]] = {}
        self._unreadable = unreadable
        self._unwritable = unwritable

    def append(
        self,
        thread_id: str,
        *,
        query: str,
        submission: dict[str, Any],
        measurement: dict[str, Any],
    ) -> Turn:
        if self._unwritable:
            raise StorageUnavailableError("the journal is not writable")
        rows = self._turns.setdefault(thread_id, [])
        turn = Turn(
            thread_id=thread_id,
            revision=len(rows),
            query=query,
            submission=submission,
            measurement=measurement,
        )
        rows.append(turn)
        return turn

    def latest(self, thread_id: str) -> Turn | None:
        if self._unreadable:
            raise StorageUnavailableError("the journal is not readable")
        rows = self._turns.get(thread_id) or []
        return rows[-1] if rows else None

    def journal(self, thread_id: str) -> Thread | None:
        if self._unreadable:
            raise StorageUnavailableError("the journal is not readable")
        rows = self._turns.get(thread_id)
        if rows is None:
            return None
        return Thread(id=thread_id, turns=tuple(rows))

    def delete(self, thread_id: str) -> bool:
        return self._turns.pop(thread_id, None) is not None

    def written(self) -> set[str]:
        """Test-only: the thread ids anything was written under."""
        return set(self._turns)


def _recorded(*stops: tuple[int, str]) -> Turn:
    """A turn whose measurement carries these stops, in this walking order."""
    return Turn(
        thread_id="t",
        revision=0,
        query="q",
        measurement={"results": [{"id": pid, "name": name} for pid, name in stops]},
    )


def test_journal_stops_read_the_measured_order() -> None:
    turn = _recorded((2, "Б"), (1, "А"))

    assert [stop.place_id for stop in thread.journal_stops(turn)] == [2, 1]
    assert [stop.name for stop in thread.journal_stops(turn)] == ["Б", "А"]


def test_journal_stops_skip_cards_without_an_id() -> None:
    turn = Turn(
        thread_id="t",
        revision=0,
        query="q",
        measurement={"results": [{"name": "без id"}, {"id": 7, "name": "с id"}]},
    )

    assert [stop.place_id for stop in thread.journal_stops(turn)] == [7]


def test_journal_stops_of_no_turn_are_empty() -> None:
    assert thread.journal_stops(None) == ()


def test_the_journal_is_the_spine_a_refinement_is_merged_onto() -> None:
    """The recorded route leads; the snapshot only adds what the journal never saw."""
    recorded = _recorded((1, "А"), (2, "Б"))
    context = RefinementBrief(
        instruction="добавь кофе",
        revision=1,
        base_points=(BriefStop(place_id=9, name="Своя точка"),),
    )

    out = thread.reconcile(context, recorded)

    assert out is not None
    assert [stop.place_id for stop in out.base_points] == [1, 2, 9]


def test_a_refinement_rebuilds_from_the_journal_when_the_snapshot_is_empty() -> None:
    """A snapshot the client lost does not cost the route its base stops."""
    context = RefinementBrief(instruction="короче", revision=1, base_points=())

    out = thread.reconcile(context, _recorded((1, "А"), (2, "Б")))

    assert out is not None
    assert [stop.place_id for stop in out.base_points] == [1, 2]


def test_a_snapshot_point_already_on_the_route_is_not_duplicated() -> None:
    context = RefinementBrief(
        instruction="короче",
        revision=1,
        base_points=(BriefStop(place_id=2, name="Б из снапшота"),),
    )

    out = thread.reconcile(context, _recorded((1, "А"), (2, "Б")))

    assert out is not None
    assert [stop.place_id for stop in out.base_points] == [1, 2]


def test_a_deleted_stop_is_left_out_of_the_journal_base() -> None:
    context = RefinementBrief(
        instruction="убери Б",
        revision=1,
        excluded_ids=(2,),
        base_points=(BriefStop(place_id=2, name="Б"),),
    )

    out = thread.reconcile(context, _recorded((1, "А"), (2, "Б")))

    assert out is not None
    assert [stop.place_id for stop in out.base_points] == [1]


def test_without_a_record_the_snapshot_stands_as_sent() -> None:
    context = RefinementBrief(
        instruction="x", revision=1, base_points=(BriefStop(place_id=5, name="П"),)
    )

    assert thread.reconcile(context, None) is context
    assert thread.reconcile(None, _recorded((1, "А"))) is None


def test_reconcile_brief_reads_the_journals_latest_turn() -> None:
    repo = FakeThreadRepository()
    repo.append(
        "run-1",
        query="первый",
        submission={},
        measurement={"results": [{"id": 3, "name": "В"}]},
    )
    brief = ReaderBrief(
        context=RefinementBrief(
            instruction="ещё",
            revision=1,
            base_points=(BriefStop(place_id=8, name="Своё"),),
        )
    )

    out = thread.reconcile_brief(repo, thread_id="run-1", brief=brief)

    assert out.context is not None
    assert [stop.place_id for stop in out.context.base_points] == [3, 8]


def test_reconcile_brief_leaves_a_fresh_request_alone() -> None:
    brief = ReaderBrief()

    assert thread.reconcile_brief(FakeThreadRepository(), thread_id="run-x", brief=brief) is brief


def test_reconcile_brief_falls_back_when_the_journal_is_unreadable() -> None:
    repo = FakeThreadRepository(unreadable=True)
    brief = ReaderBrief(
        context=RefinementBrief(
            instruction="x", revision=1, base_points=(BriefStop(place_id=8, name="С"),)
        )
    )

    assert thread.reconcile_brief(repo, thread_id="run-1", brief=brief) is brief


def test_record_appends_a_turn() -> None:
    repo = FakeThreadRepository()

    thread.record(
        repo,
        thread_id="run-1",
        query="привет",
        submission={"ordered_ids": [1]},
        measurement={"status": "ready"},
    )

    latest = repo.latest("run-1")
    assert latest is not None
    assert latest.query == "привет"
    assert latest.measurement["status"] == "ready"


def test_record_swallows_a_journal_write_failure() -> None:
    """A journal that cannot be written is a lost replay, never a failed plan."""
    thread.record(
        FakeThreadRepository(unwritable=True),
        thread_id="run-1",
        query="привет",
        submission={},
        measurement={},
    )


class _Taxonomy:
    """The one repository method `/routes/generate` still reaches after planning."""

    def all(self) -> list[Any]:
        return []


def _canned_outcome() -> AgentOutcome:
    return AgentOutcome(
        submission=PlanSubmission(ordered_ids=[1, 2], narrative="план"),
        requirements=TripRequirements(),
        measured={"status": "ready", "results": [{"id": 1, "name": "А"}]},
    )


def _app(monkeypatch, repo: FakeThreadRepository, captured: list[ReaderBrief]) -> TestClient:
    """A bare app over the routes router, with the run and the response replaced."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def fake_plan(query: str, brief: ReaderBrief, **kwargs: Any) -> AgentOutcome:
        captured.append(brief)
        return _canned_outcome()

    monkeypatch.setattr("agent.client.plan_with_agent", fake_plan)
    monkeypatch.setattr(
        "api.responses.route_response",
        lambda *args, **kwargs: RouteResponse(
            parsed=ParsedQuery(), points=[], shape={}, summary=RouteSummary()
        ),
    )
    app = FastAPI()
    app.include_router(routes_api.router)
    app.state.repos = SimpleNamespace(threads=repo, taxonomy=_Taxonomy())
    return TestClient(app)


def test_generate_lets_the_journal_lead_the_refinement(monkeypatch) -> None:
    repo = FakeThreadRepository()
    repo.append(
        "run-1",
        query="первый",
        submission={},
        measurement={"results": [{"id": 3, "name": "В"}, {"id": 4, "name": "Г"}]},
    )
    captured: list[ReaderBrief] = []
    client = _app(monkeypatch, repo, captured)

    response = client.post(
        "/routes/generate",
        json={
            "query": "добавь кофе",
            "session_id": "run-1",
            "context": {
                "instruction": "добавь кофе",
                "revision": 1,
                "base_points": [{"id": 9, "name": "Пин", "lat": 53.68, "lon": 23.83}],
            },
        },
    )

    assert response.status_code == 200, response.text
    brief = captured[0]
    assert brief.context is not None
    assert [stop.place_id for stop in brief.context.base_points] == [3, 4, 9]


def test_generate_records_the_turn_under_the_session(monkeypatch) -> None:
    repo = FakeThreadRepository()
    client = _app(monkeypatch, repo, [])

    response = client.post("/routes/generate", json={"query": "прогулка", "session_id": "run-9"})

    assert response.status_code == 200, response.text
    latest = repo.latest("run-9")
    assert latest is not None
    assert latest.query == "прогулка"
    assert latest.measurement["status"] == "ready"
    assert latest.submission["ordered_ids"] == [1, 2]


def test_generate_without_a_session_keeps_no_thread(monkeypatch) -> None:
    repo = FakeThreadRepository()
    client = _app(monkeypatch, repo, [])

    response = client.post("/routes/generate", json={"query": "прогулка"})

    assert response.status_code == 200, response.text
    assert repo.written() == set()


def test_generate_reports_when_planning_produced_nothing(monkeypatch) -> None:
    repo = FakeThreadRepository()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.client.plan_with_agent", lambda *args, **kwargs: None)
    app = FastAPI()
    app.include_router(routes_api.router)
    app.state.repos = SimpleNamespace(threads=repo, taxonomy=_Taxonomy())

    response = TestClient(app).post(
        "/routes/generate", json={"query": "прогулка", "session_id": "run-9"}
    )

    assert response.status_code == 503
    assert repo.written() == set()


def test_the_postgres_journal_round_trips(db_repos) -> None:
    """The real store, skipped until the `turns` schema is applied to the database."""
    repo = db_repos.threads
    thread_id = f"test-{uuid.uuid4().hex}"
    try:
        repo.journal(thread_id)
    except StorageUnavailableError:
        pytest.skip("the threads/turns schema is not migrated in this database")
    try:
        first = repo.append(
            thread_id,
            query="первый",
            submission={"ordered_ids": [1]},
            measurement={"results": [{"id": 1, "name": "А"}]},
        )
        second = repo.append(
            thread_id, query="второй", submission={}, measurement={"status": "ready"}
        )

        assert (first.revision, second.revision) == (0, 1)
        assert second.measurement["status"] == "ready"
        assert second.submission == {}

        latest = repo.latest(thread_id)
        assert latest is not None
        assert latest.revision == 1

        whole = repo.journal(thread_id)
        assert whole is not None
        assert [turn.revision for turn in whole.turns] == [0, 1]
        assert isinstance(whole.created_at, datetime)
    finally:
        repo.delete(thread_id)

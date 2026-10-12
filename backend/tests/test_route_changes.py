"""What a refinement tells the tourist it changed.

Two things are exercised here, both offline. The diff itself — previous route
against the final points — is a pure function over stops and places. The source
it diffs against is the journal's last turn where the server has one, and the
client's re-sent snapshot only as a fallback; `previous_stops` is checked both
ways, and the router is checked to wire the journal in. No model and no migrated
database are needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import thread
from agent.client import AgentOutcome
from agent.models import BriefStop, RefinementBrief
from agent.schema import PlanSubmission
from api.models.routes import ParsedQuery, Place, RouteResponse, RouteSummary
from api.responses import route_changes, route_response
from api.routers import routes as routes_api
from db.models.place import Place as PlaceRow
from db.models.thread import Turn
from db.store.errors import StorageUnavailableError
from routing.models import CategoryFacts, TripRequirements

NAMES = {1: "А", 2: "Б", 3: "В", 4: "Г", 5: "Д"}


class FakeThreadRepository:
    """The journal, in memory: enough of it for a read and an append."""

    def __init__(self, *, unreadable: bool = False) -> None:
        self._turns: dict[str, list[Turn]] = {}
        self._unreadable = unreadable

    def append(
        self,
        thread_id: str,
        *,
        query: str,
        submission: dict[str, Any],
        measurement: dict[str, Any],
    ) -> Turn:
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


def _points(*pairs: tuple[int, str]) -> list[Place]:
    return [Place(id=pid, name=name, category=None, lat=0.0, lon=0.0) for pid, name in pairs]


def _brief_stops(*pairs: tuple[int, str]) -> tuple[BriefStop, ...]:
    return tuple(BriefStop(place_id=pid, name=name) for pid, name in pairs)


# --- the diff itself ---------------------------------------------------------


def test_a_refinement_that_removes_a_stop_reports_it_removed() -> None:
    previous = _brief_stops((1, "А"), (2, "Б"), (3, "В"))

    changes = route_changes(previous, _points((1, "А"), (3, "В")))

    assert changes is not None
    assert [(c.id, c.name) for c in changes.removed] == [(2, "Б")]
    assert changes.added == []
    assert changes.kept == 2


def test_a_refinement_that_adds_a_stop_reports_it_added() -> None:
    previous = _brief_stops((1, "А"), (2, "Б"))

    changes = route_changes(previous, _points((1, "А"), (2, "Б"), (4, "Г")))

    assert changes is not None
    assert [(c.id, c.name) for c in changes.added] == [(4, "Г")]
    assert changes.removed == []
    assert changes.kept == 2


def test_a_refinement_that_keeps_everything_reports_no_change() -> None:
    previous = _brief_stops((1, "А"), (2, "Б"), (3, "В"))

    changes = route_changes(previous, _points((1, "А"), (2, "Б"), (3, "В")))

    assert changes is not None
    assert changes.added == []
    assert changes.removed == []
    assert changes.kept == 3


def test_a_stop_absent_from_the_final_points_is_never_kept() -> None:
    """A stop the code dropped is `removed`, not quietly counted as kept."""
    previous = _brief_stops((1, "А"), (2, "Б"))

    changes = route_changes(previous, _points((1, "А")))

    assert changes is not None
    assert [c.id for c in changes.removed] == [2]
    assert changes.kept == 1


def test_a_fresh_generation_states_no_change() -> None:
    """No previous route is not an empty diff — there is nothing to compare."""
    assert route_changes((), _points((1, "А"))) is None


def test_a_pinned_stop_without_an_id_is_matched_by_name() -> None:
    """A hand-pinned snapshot point carries no id; its name is the only handle."""
    previous = (BriefStop(place_id=None, name="Своя точка"),)

    changes = route_changes(previous, _points((1, "Своя точка")))

    assert changes is not None
    assert changes.removed == []
    assert changes.kept == 1
    # The named stop is the one the point matches, so it is not a fresh addition.
    assert changes.added == []


# --- the route_response producer --------------------------------------------


def _row(pid: int) -> PlaceRow:
    return PlaceRow(id=pid, name=NAMES[pid], lat=53.0, lon=23.0, visit_minutes=10)


class _Places:
    def __init__(self, ids: list[int]) -> None:
        self._by_id = {pid: _row(pid) for pid in ids}

    def get_by_ids(self, ids: list[int]) -> list[PlaceRow]:
        return [self._by_id[pid] for pid in ids if pid in self._by_id]


class _Taxonomy:
    def all(self) -> list[Any]:
        return []


class _Repos:
    def __init__(self, ids: list[int]) -> None:
        self.places = _Places(ids)
        self.taxonomy = _Taxonomy()


def _built_response(previous: tuple[BriefStop, ...] | None, final: list[int]) -> RouteResponse:
    outcome = AgentOutcome(
        submission=PlanSubmission(ordered_ids=list(final)),
        requirements=TripRequirements(),
        measured={
            "status": "ready",
            "results": [{"id": pid, "name": NAMES[pid]} for pid in final],
        },
    )
    return route_response(
        outcome,
        repos=_Repos([1, 2, 3, 4, 5]),
        facts=CategoryFacts.from_rows([]),
        previous=previous,
    )


def test_route_response_fills_changes_on_a_refinement() -> None:
    response = _built_response(_brief_stops((1, "А"), (2, "Б"), (3, "В")), [1, 3])

    assert response.changes is not None
    assert [c.id for c in response.changes.removed] == [2]
    assert response.changes.kept == 2


def test_route_response_leaves_a_fresh_generation_without_changes() -> None:
    assert _built_response(None, [1, 2]).changes is None


# --- which source the previous route comes from ------------------------------


def test_the_journal_outranks_the_snapshot_for_the_previous_route() -> None:
    repo = FakeThreadRepository()
    repo.append(
        "run-1",
        query="первый",
        submission={},
        measurement={"results": [{"id": 1, "name": "А"}, {"id": 2, "name": "Б"}]},
    )
    context = RefinementBrief(
        instruction="ещё", revision=1, base_points=(BriefStop(place_id=9, name="Своё"),)
    )

    stops = thread.previous_stops(repo, thread_id="run-1", context=context)

    assert [stop.place_id for stop in stops] == [1, 2]


def test_without_a_record_the_snapshot_is_the_previous_route() -> None:
    context = RefinementBrief(
        instruction="ещё", revision=1, base_points=(BriefStop(place_id=9, name="Своё"),)
    )

    stops = thread.previous_stops(FakeThreadRepository(), thread_id="run-1", context=context)

    assert [stop.place_id for stop in stops] == [9]


def test_an_unreadable_journal_falls_back_to_the_snapshot() -> None:
    context = RefinementBrief(
        instruction="ещё", revision=1, base_points=(BriefStop(place_id=9, name="Своё"),)
    )

    stops = thread.previous_stops(
        FakeThreadRepository(unreadable=True), thread_id="run-1", context=context
    )

    assert [stop.place_id for stop in stops] == [9]


def test_a_fresh_request_has_no_previous_route() -> None:
    assert thread.previous_stops(FakeThreadRepository(), thread_id="run-1", context=None) == ()


# --- the router wires the journal in -----------------------------------------


def _canned_outcome() -> AgentOutcome:
    return AgentOutcome(
        submission=PlanSubmission(ordered_ids=[3, 4]),
        requirements=TripRequirements(),
        measured={"status": "ready", "results": [{"id": 3, "name": "В"}, {"id": 4, "name": "Г"}]},
    )


def test_generate_diffs_against_the_journals_last_turn(monkeypatch) -> None:
    repo = FakeThreadRepository()
    repo.append(
        "run-1",
        query="первый",
        submission={},
        measurement={"results": [{"id": 3, "name": "В"}, {"id": 4, "name": "Г"}]},
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("agent.client.plan_with_agent", lambda *a, **k: _canned_outcome())

    seen: dict[str, Any] = {}

    def fake_response(outcome: Any, **kwargs: Any) -> RouteResponse:
        seen["previous"] = kwargs.get("previous")
        return RouteResponse(parsed=ParsedQuery(), points=[], shape={}, summary=RouteSummary())

    monkeypatch.setattr("api.responses.route_response", fake_response)

    app = FastAPI()
    app.include_router(routes_api.router)
    app.state.repos = SimpleNamespace(threads=repo, taxonomy=_Taxonomy())

    response = TestClient(app).post(
        "/routes/generate",
        json={
            "query": "убери Г",
            "session_id": "run-1",
            "context": {
                "instruction": "убери Г",
                "revision": 1,
                "base_points": [{"id": 3, "name": "В", "lat": 53.0, "lon": 23.0}],
            },
        },
    )

    assert response.status_code == 200, response.text
    assert [stop.place_id for stop in seen["previous"]] == [3, 4]

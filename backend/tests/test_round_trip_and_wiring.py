"""Wiring regressions: the defects where the code existed but was not connected."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.models import Requirement, TripRequirements
from core import constants
from core.errors import UpstreamUnavailable
from db.store.areas import PostgresAreaRepository
from db.store.places import PostgresPlaceRepository
from db.store.registry import Repositories
from planner import (
    optimize as optimize_mod,
    pipeline as pipeline_mod,
    plan_tail as plan_tail_mod,
    render as render_mod,
)
from planner.cost import (
    REASON_MUST_VISIT_UNROUTABLE,
    PrunedStop,
)
from planner.models import (
    Candidate,
    CostMatrix,
    IntentDecision,
    IntentResult,
    ResolvedConstraints,
)
from planner.resolve import resolve
from planner.valhalla.types import RouteResult, RouteStatus
from planner.validate import validate
from planner.verify import (
    REASON_MUST_VISIT_UNROUTABLE as VERIFY_UNROUTABLE,
    overall_status,
    verify,
)


def _cand(pid: int, name: str, category: str, lat: float = 53.68, lon: float = 23.83) -> Candidate:
    return Candidate(id=pid, name=name, category=category, lat=lat, lon=lon)


def _reqs(*requirements: Requirement) -> TripRequirements:
    return TripRequirements(requirements=list(requirements))


def test_resolve_carries_the_round_trip_choice():
    places = PostgresPlaceRepository()
    intent = IntentResult(decision=IntentDecision(), source="agent")
    plain = resolve(intent, places=places)
    closed = resolve(intent, places=places, explicit_round_trip=True)

    assert plain.round_trip is False
    assert closed.round_trip is True


def test_validate_counts_the_return_leg_for_a_round_trip():
    """The walk home is real time: a closed tour must not look cheaper."""
    route = [_cand(1, "A", "замок"), _cand(2, "B", "музей"), _cand(3, "C", "парк")]
    cost = CostMatrix(
        walk_seconds=[[0.0, 300.0, 400.0], [300.0, 0.0, 250.0], [400.0, 250.0, 0.0]],
        visit_minutes=[10, 10, 10],
        indices=[0, 1, 2],
    )
    info = {"order": [0, 1, 2], "algorithm": "brute_open"}

    open_plan = validate(route, cost, ResolvedConstraints(), info)
    closed_plan = validate(route, cost, ResolvedConstraints(round_trip=True), info)

    assert closed_plan.walk_seconds == open_plan.walk_seconds + 400.0


def test_render_closes_the_tour_when_asked(monkeypatch):
    """The return leg is asked of Valhalla, so the drawn line includes it."""
    captured: list[list[dict]] = []

    def fake_route_through(locations, costing="pedestrian", language="ru", timeout=None):
        captured.append([dict(loc) for loc in locations])
        return RouteResult(
            status=RouteStatus.USABLE,
            shape={"type": "LineString", "coordinates": [[23.83, 53.68], [23.84, 53.69]]},
            summary={"length": 1.0, "time": 600.0},
            maneuvers=[],
            language="ru",
        )

    monkeypatch.setattr(render_mod, "route_through", fake_route_through)
    route = [
        _cand(1, "A", "замок", lat=53.68, lon=23.83),
        _cand(2, "B", "музей", lat=53.70, lon=23.85),
    ]

    render_mod.render(route, round_trip=True)

    locs = captured[0]
    assert len(locs) == 3, "start, stop, and the walk home"
    assert (locs[0]["lat"], locs[0]["lon"]) == (53.68, 23.83) == (locs[-1]["lat"], locs[-1]["lon"])
    assert locs[1]["type"] == "via"
    assert locs[-1]["type"] == "break"

    captured.clear()
    render_mod.render(route, round_trip=False)
    assert len(captured[0]) == 2, "an open tour asks for no return leg"


def _island_cost() -> CostMatrix:
    """a→b is unroutable, everything else routes (the road-island case)."""
    bad = float(constants.UNREACHABLE_S)
    return CostMatrix(
        walk_seconds=[[0.0, bad, 600.0], [bad, 0.0, 700.0], [600.0, 700.0, 0.0]],
        visit_minutes=[10, 10, 10],
        indices=[0, 1, 2],
    )


def test_pipeline_pruner_keeps_a_mandatory_stop_and_returns_the_report():
    """``_prune_unroutable`` must forward ``must_visit_ids`` and hand back the reason."""
    a = _cand(1, "Волковыск", "памятник")
    b = _cand(2, "Каплица на острове", "костёл", lat=53.007, lon=23.917)
    c = _cand(3, "Гродно", "памятник")

    route, report = optimize_mod._prune_unroutable([a, b, c], [a, b, c], _island_cost(), [2])

    assert [x.id for x in route] == [1, 2, 3], "the mandatory stop stays on the route"
    assert [p.id for p in report] == [2]
    assert report[0].reason == REASON_MUST_VISIT_UNROUTABLE


def test_the_order_follows_the_stops_a_prune_removed():
    """A prune shortens the route; ``info["order"]`` must follow it, not the dead.
    The stale order made the lengths disagree and validate fell back to ``range(n)``.
    """
    a = _cand(1, "А", "замок")
    b = _cand(2, "Б", "музей")
    c = _cand(3, "В", "парк", lon=23.86)
    cost = _island_cost()

    info = optimize_mod._order_after_prune({"order": [0, 1, 2]}, [a, c], [a, b, c])
    assert info["order"] == [0, 2], "index 1 (the pruned stop) is gone"

    plan = validate([a, c], cost, ResolvedConstraints(), info)
    assert plan.walk_seconds == 600.0, "priced a→c, not the unroutable a→b"
    assert plan.trace["walk_times_unknown"] == 0


def test_the_optional_tour_reorder_is_bounded_and_degrades(monkeypatch):
    """The Valhalla re-order must get a short single-shot budget.
    It is an optimisation, not a requirement; a failure falls back to the planned order.
    """
    captured: dict = {}

    def fake(coords, costing="pedestrian", timeout=None, retries=None):
        captured["timeout"] = timeout
        captured["retries"] = retries
        raise UpstreamUnavailable("the solver will not answer this tour")

    monkeypatch.setattr(optimize_mod, "valhalla_optimized_route", fake)
    route = [_cand(1, "A", "замок"), _cand(2, "B", "музей"), _cand(3, "C", "парк")]
    info = {"order": [0, 1, 2]}

    out_route, out_info = optimize_mod._valhalla_order(
        route,
        info,
        costing="auto",
        timeout=constants.VALHALLA_ORDER_TIMEOUT_S,
        retries=constants.VALHALLA_ORDER_RETRIES,
    )

    assert out_route == route and out_info == info, "the planned order stands"
    assert captured["timeout"] == constants.VALHALLA_ORDER_TIMEOUT_S
    assert captured["retries"] == 0
    assert constants.VALHALLA_ORDER_TIMEOUT_S < constants.VALHALLA_TIMEOUT_S


def test_verify_marks_a_kept_but_unroutable_must_visit_as_unmet():
    """Presence in the stop list does not outrank Valhalla's own verdict."""
    stop = _cand(5, "Каплица на острове", "костёл")
    plan = {
        "route": [stop, _cand(1, "Музей", "музей")],
        "trace": {
            "unroutable_stops": [
                {"id": 5, "name": "Каплица на острове", "reason": VERIFY_UNROUTABLE}
            ]
        },
    }
    reqs = _reqs(Requirement(kind="must_visit", strength="hard", place_id=5, name="Каплица"))

    result = verify(reqs, plan, {"type": "LineString", "coordinates": [[1, 2], [3, 4]]})

    assert result[0].status == "unmet"
    assert result[0].reason == VERIFY_UNROUTABLE
    assert overall_status(reqs) == "infeasible"


def test_validate_records_the_pruner_report_in_the_trace():
    """Whatever the pruner reported ends up where the verifier reads it."""
    route = [_cand(1, "A", "замок"), _cand(2, "B", "музей")]
    cost = CostMatrix(
        walk_seconds=[[0.0, 300.0], [300.0, 0.0]], visit_minutes=[10, 10], indices=[0, 1]
    )
    report = [PrunedStop(route[1], REASON_MUST_VISIT_UNROUTABLE)]

    plan = validate(route, cost, ResolvedConstraints(), {"order": [0, 1]}, prune_report=report)

    assert plan.trace["unroutable_stops"] == [
        {"id": 2, "name": "B", "reason": REASON_MUST_VISIT_UNROUTABLE}
    ]


class _FakeCursor:
    """A dict-row cursor over a fixed set of place rows.

    The repository opens it with ``row_factory=dict_row``; the rows here are
    already the mappings ``place_from_row`` reads.
    """

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def execute(self, *_args, **_kwargs) -> None:
        return None

    def fetchall(self) -> list[dict]:
        return [dict(row) for row in self._rows]

    def fetchone(self) -> dict | None:
        rows = self.fetchall()
        return rows[0] if rows else None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class _FakeConnection:
    """A psycopg-shaped connection over a fixed set of place rows."""

    def __init__(self, rows: list[dict]) -> None:
        self.closed = False
        self._rows = rows

    def cursor(self, row_factory=None) -> _FakeCursor:
        return _FakeCursor(self._rows)

    def close(self) -> None:
        self.closed = True


def _row(pid: int, name: str, category: str, lat: float, lon: float) -> dict:
    return {
        "id": pid,
        "name": name,
        "category": category,
        "lat": lat,
        "lon": lon,
        "blurb": None,
        "fun_fact": None,
        "fun_facts": None,
        "links": None,
        "opening_hours": None,
        "ticket_price": None,
        "town": None,
        "district": None,
        "visit_minutes": None,
        "photo_url": None,
        "photo_author": None,
        "photo_license": None,
        "photo_source": None,
    }


def test_reroute_returns_a_route_instead_of_raising(monkeypatch):
    """``POST /routes/reroute`` runs offline: it reads no query, so it needs no model."""
    rows = [
        _row(1, "Старый замок", "замок", 53.6788, 23.8230),
        _row(2, "Новый замок", "дворец", 53.6849, 23.8310),
    ]
    connection = _FakeConnection(rows)
    pipeline = pipeline_mod.Pipeline(
        repos=Repositories(
            places=PostgresPlaceRepository(connect=lambda: connection),
            areas=PostgresAreaRepository(),
        )
    )

    monkeypatch.setattr(
        plan_tail_mod,
        "compute_cost_matrix",
        lambda *a, **k: CostMatrix(
            walk_seconds=[[0.0, 300.0], [300.0, 0.0]], visit_minutes=[40, 30], indices=[0, 1]
        ),
    )
    monkeypatch.setattr(
        pipeline_mod,
        "render",
        lambda *a, **k: (
            {"type": "LineString", "coordinates": [[23.8230, 53.6788], [23.8310, 53.6849]]},
            {"length": 1.2, "time": 300.0},
            "usable",
        ),
    )

    response = pipeline.reroute([1, 2])

    assert [p.name for p in response.points] == ["Старый замок", "Новый замок"]
    assert response.shape["type"] == "LineString"
    assert response.summary.time_seconds == 300.0
    assert response.costing == "pedestrian"

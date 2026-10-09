"""Wiring regressions: the three defects that were "code present, not connected".

Every one of these was invisible to the existing suite because the parts were
tested in isolation and the *seam* between them was not:

  * ``Pipeline.reroute`` called ``extract_intent`` without importing it, so the
    documented ``POST /routes/reroute`` answered a NameError → HTTP 500. The
    unit tests import ``extract_intent`` from ``intent``, never from ``pipeline``,
    so nothing noticed;
  * a round-trip request («круговой маршрут») was accepted, echoed back in the
    response and then ignored: the tour was drawn and budgeted as an open one;
  * an unroutable MANDATORY stop was pruned away silently — the pipeline never
    handed the pruner its ``must_visit_ids`` nor the pruner's report to
    ``validate``, so ``verify`` could only ever say "absent".

No network, no DB.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.planner import (
    intent as intent_mod,
    optimize as optimize_mod,
    pipeline as pipeline_mod,
    render as render_mod,
)
from agent.planner.cost import (
    REASON_MUST_VISIT_UNROUTABLE,
    PrunedStop,
)
from agent.planner.intent import fallback_intent
from agent.planner.resolve import resolve
from agent.planner.validate import validate
from agent.planner.verify import (
    REASON_MUST_VISIT_UNROUTABLE as VERIFY_UNROUTABLE,
    overall_status,
    verify,
)
from contracts.planner import Candidate, CostMatrix, ResolvedConstraints
from core.errors import UpstreamUnavailable
from domain import constants
from domain.requirements import Requirement, TripRequirements
from infra.valhalla_client import RouteResult, RouteStatus
from store import search as search_mod


def _cand(pid: int, name: str, category: str, lat: float = 53.68, lon: float = 23.83) -> Candidate:
    return Candidate(id=pid, name=name, category=category, lat=lat, lon=lon)


def _reqs(*requirements: Requirement) -> TripRequirements:
    return TripRequirements(requirements=list(requirements))


# 1. the import that /routes/reroute needs

def test_pipeline_exposes_extract_intent():
    """The module the reroute handler runs in must actually define the name.

    ``resolve(extract_intent(...))`` in ``Pipeline.reroute`` raised
    ``NameError: name 'extract_intent' is not defined`` because the symbol was
    never imported into ``pipeline``; this asserts the seam, not the function.
    """
    assert hasattr(pipeline_mod, "extract_intent")
    assert pipeline_mod.extract_intent is intent_mod.extract_intent
    assert callable(pipeline_mod.extract_intent)


# 2. round trip is applied, not just echoed

def test_resolve_carries_the_round_trip_choice():
    db = object()  # never touched: no named places, no prohibitions
    plain = resolve(fallback_intent("прогулка по парку"), db=db)
    closed = resolve(fallback_intent("прогулка по парку"), db=db, explicit_round_trip=True)

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

    # C → A is 400 s; open tours never pay it, closed ones do.
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
    route = [_cand(1, "A", "замок", lat=53.68, lon=23.83), _cand(2, "B", "музей", lat=53.70, lon=23.85)]

    render_mod.render(route, round_trip=True)

    locs = captured[0]
    assert len(locs) == 3, "start, stop, and the walk home"
    assert (locs[0]["lat"], locs[0]["lon"]) == (53.68, 23.83) == (locs[-1]["lat"], locs[-1]["lon"])
    assert locs[1]["type"] == "via"
    assert locs[-1]["type"] == "break"

    captured.clear()
    render_mod.render(route, round_trip=False)
    assert len(captured[0]) == 2, "an open tour asks for no return leg"


# 3. the pruner's report reaches the verifier

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

    route, report = pipeline_mod._prune_unroutable([a, b, c], [a, b, c], _island_cost(), [2])

    assert [x.id for x in route] == [1, 2, 3], "the mandatory stop stays on the route"
    assert [p.id for p in report] == [2]
    assert report[0].reason == REASON_MUST_VISIT_UNROUTABLE


def test_the_order_follows_the_stops_a_prune_removed():
    """A prune shortens the route; ``info["order"]`` must follow it, not the dead.

    ``info["order"]`` indexes the cost matrix and validate prices the walk
    through it. The stale order made the lengths disagree, validate fell back to
    ``range(n)`` and summed the first n candidates' legs — the walk of stops that
    were pruned, reported as if it were the survivor's.
    """
    a = _cand(1, "А", "замок")
    b = _cand(2, "Б", "музей")  # the stop the prune removes
    c = _cand(3, "В", "парк", lon=23.86)
    cost = _island_cost()  # a→b unroutable, a↔c and b↔c route

    info = pipeline_mod._order_after_prune({"order": [0, 1, 2]}, [a, c], [a, b, c])
    assert info["order"] == [0, 2], "index 1 (the pruned stop) is gone"

    plan = validate([a, c], cost, ResolvedConstraints(), info)
    assert plan.walk_seconds == 600.0, "priced a→c, not the unroutable a→b"
    assert plan.trace["walk_times_unknown"] == 0


def test_the_optional_tour_reorder_is_bounded_and_degrades(monkeypatch):
    """The Valhalla re-order must get a short single-shot budget.

    It is an optimisation, not a requirement: over a region-wide tour the solver
    answers nothing at all, and with the shared 20 s / 2-retry defaults the call
    burned ≈61 s (3×20 s + backoff) before the matrix order was used anyway. The
    cap and the zero retries are what keep that tail off the request — and a
    failure still falls back to the planned order instead of propagating.
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


# 4. the reroute endpoint runs end to end

class _FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.description = [
            SimpleNamespace(name=name) for name in search_mod.CATEGORY_COLS.split(", ")
        ]

    def execute(self, *_args, **_kwargs) -> None:
        return None

    def fetchall(self) -> list[tuple]:
        cols = [d.name for d in self.description]
        return [tuple(self._row.get(c) for c in cols) for self._row in self._rows]

    def fetchone(self):
        return self.fetchall()[0]

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class _FakeDB:
    """A psycopg-shaped connection over a fixed set of place rows."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._rows)


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
    """``POST /routes/reroute`` used to die two ways: an undefined
    ``extract_intent`` and a three-value ``render()`` unpacked into two names.
    Both are name/arity errors that the unit tests could not see — the endpoint
    itself had no test. This runs the whole handler offline."""
    rows = [
        _row(1, "Старый замок", "замок", 53.6788, 23.8230),
        _row(2, "Новый замок", "дворец", 53.6849, 23.8310),
    ]
    pipeline = pipeline_mod.Pipeline(db=_FakeDB(rows))

    monkeypatch.setattr(
        pipeline_mod,
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

"""What the planner does once the engine says it cannot reach a stop — offline."""

from __future__ import annotations

from typing import Any

import pytest

from agent.tools import plan
from core import constants
from routing.models import (
    Candidate,
    CostMatrix,
    OriginAnchor,
    ResolvedConstraints,
    WalkRequest,
)

BROKEN = 2


def _candidate(index: int) -> Candidate:
    return Candidate(
        id=index + 1, name=f"точка {index}", category="музей", lat=53.67 + index * 0.001, lon=23.83
    )


def _line_cost() -> CostMatrix:
    """Three stops in a row and one the engine cannot reach at all."""
    stations = [0.0, 100.0, None, 300.0]

    def leg(i: int, j: int) -> float:
        if stations[i] is None or stations[j] is None:
            return float(constants.UNREACHABLE_S)
        return abs(stations[i] - stations[j])

    n = len(stations)
    return CostMatrix(
        walk_seconds=[[0.0 if i == j else leg(i, j) for j in range(n)] for i in range(n)],
        visit_minutes=[10] * n,
        indices=list(range(n)),
        anchor=OriginAnchor(from_origin=[0.0, 100.0, 9999.0, 300.0], to_origin=[0.0] * n),
    )


def test_the_survivors_are_ordered_again_not_just_reindexed() -> None:
    """The stop that was dropped was part of the walk: what is left is another one."""
    candidates = [_candidate(i) for i in range(4)]
    cost = _line_cost()
    survivors = [candidates[3], candidates[1], candidates[0]]
    info: dict[str, Any] = {"algorithm": "regret_2", "order": [3, 1, 0]}

    route, fresh = plan._reorder_after_prune(
        survivors, candidates, cost, ResolvedConstraints(time_budget_minutes=None), info
    )

    assert [c.id for c in route] == [1, 2, 4], "the walk now starts nearest the origin"
    assert fresh["order"] == [0, 1, 3], "indices still point into the whole matrix"
    assert fresh["reordered_after_prune"] is True
    assert fresh["algorithm"] == "brute_open", "the survivors were ordered from scratch"


def test_a_second_prune_leaves_the_indices_pointing_at_the_stops_that_are_left() -> None:
    """`validate` reads the whole matrix, so an order longer than the route is a
    silent fall back to the order the stops arrived in.
    """
    candidates = [_candidate(i) for i in range(4)]
    info: dict[str, Any] = {"algorithm": "brute_open", "order": [3, 1, 0]}
    left = [candidates[3], candidates[0]]

    fresh = plan.order_after_prune(info, left, candidates)

    assert fresh["order"] == [3, 0]


def test_a_line_missing_a_leg_is_recorded_as_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gap in the drawn line is a fact about the answer, so the answer says it."""
    monkeypatch.setattr(
        plan,
        "render",
        lambda *_args, **_kwargs: (
            {"type": "LineString", "coordinates": [[23.83, 53.67]]},
            {"time": 60.0},
            "partial",
        ),
    )
    provenance: dict[str, Any] = {}

    shape, summary = plan._drawn(
        [_candidate(0)], WalkRequest(stop_ids=[1]), "pedestrian", provenance
    )

    assert provenance["geometry"] == "a leg is missing"
    assert shape
    assert summary


def test_a_whole_line_is_recorded_as_nothing_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plan,
        "render",
        lambda *_args, **_kwargs: (
            {"type": "LineString", "coordinates": [[23.83, 53.67]]},
            {"time": 60.0},
            "usable",
        ),
    )
    provenance: dict[str, Any] = {}

    plan._drawn([_candidate(0)], WalkRequest(stop_ids=[1]), "pedestrian", provenance)

    assert "geometry" not in provenance

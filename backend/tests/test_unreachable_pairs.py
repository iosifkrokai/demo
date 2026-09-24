"""Unreachable pairs must degrade the route, never crash the request.

Regression for: one POI with no pedestrian edges nearby (Grodno fortress,
53.597305,23.800828) made Valhalla answer 500 for every pair involving it;
marking the pair unreachable then blew up as
`OverflowError: cannot convert float infinity to integer` in the optimizer.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import constants
from agent.models import Candidate, CostMatrix
from agent.planner import cost as cost_mod
from agent.planner.optimize import _budget_constrain, total_seconds

BIG = float(constants.UNREACHABLE_S)


def _c(i: int, name: str) -> Candidate:
    return Candidate(
        id=i,
        name=name,
        category="замок",
        lat=53.67 + i / 100,
        lon=23.82 + i / 100,
        rrf_score=1.0 - i / 10,
        relevance=1.0 - i / 10,
        visit_minutes_db=30,
        town="Гродно",
        district="Гродненский район",
    )


def test_walk_cost_saturates_on_non_finite_cell():
    matrix = [[0.0, float("inf")], [float("inf"), 0.0]]
    assert cost_mod.walk_cost([0, 1], matrix) == BIG


def test_total_seconds_survives_the_sentinel():
    matrix = [[0.0, BIG], [BIG, 0.0]]
    visits = [30, 30]
    total = total_seconds([0, 1], matrix, visits)
    assert isinstance(total, int)
    assert total > 0


def test_budget_constrain_drops_the_unreachable_stop():
    """Two good stops plus one poisoned one: the tour keeps the good pair."""
    candidates = [_c(0, "Старый замок"), _c(1, "Новый замок"), _c(2, "Опорный пункт")]
    # 2 unreachable against everyone, 0 and 1 walkable between themselves
    matrix = [
        [0.0, 600.0, BIG],
        [600.0, 0.0, BIG],
        [BIG, BIG, 0.0],
    ]
    visits = [30, 30, 30]
    order, dropped = _budget_constrain(candidates, [0, 1, 2], matrix, visits, 7200, set())

    assert 2 not in order, "the unreachable stop must be dropped"
    assert set(order) == {0, 1}
    assert dropped == 1


def test_drop_unreachable_filters_candidate_and_matrix():
    candidates = [_c(0, "Старый замок"), _c(1, "Новый замок"), _c(2, "Опорный пункт")]
    matrix = [
        [0.0, 600.0, BIG],
        [600.0, 0.0, BIG],
        [BIG, BIG, 0.0],
    ]
    cost = CostMatrix(walk_seconds=matrix, visit_minutes=[30, 30, 30], indices=[0, 1, 2])

    kept, new_cost = cost_mod.drop_unreachable(candidates, cost)

    assert [c.name for c in kept] == ["Старый замок", "Новый замок"]
    assert new_cost.walk_seconds == [[0.0, 600.0], [600.0, 0.0]]
    assert new_cost.indices == [0, 1]
    assert all(math.isfinite(v) for row in new_cost.walk_seconds for v in row)


def test_drop_unreachable_keeps_a_healthy_pool_untouched():
    candidates = [_c(0, "A"), _c(1, "B"), _c(2, "C")]
    matrix = [[0.0, 100.0, 200.0], [100.0, 0.0, 150.0], [200.0, 150.0, 0.0]]
    cost = CostMatrix(walk_seconds=matrix, visit_minutes=[30, 30, 30], indices=[0, 1, 2])

    kept, new_cost = cost_mod.drop_unreachable(candidates, cost)

    assert len(kept) == 3
    assert new_cost.walk_seconds == matrix

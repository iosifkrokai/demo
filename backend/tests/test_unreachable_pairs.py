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
import threading
from collections.abc import Callable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.planner import cost as cost_mod
from agent.planner.optimize import _budget_constrain, total_seconds
from contracts.planner import Candidate, CostMatrix
from domain import constants

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


def _within(deadline: float, fn: Callable[..., list[int]], *args) -> list[int]:
    """Run `fn` and fail the test if it does not finish in time.

    A daemon thread, not a signal or an executor: a search that never returns
    must fail here in seconds instead of hanging the whole suite, and the
    leftover thread must not keep the process alive at exit.
    """
    box: dict[str, list[int]] = {}
    worker = threading.Thread(target=lambda: box.setdefault("value", fn(*args)), daemon=True)
    worker.start()
    worker.join(deadline)
    assert not worker.is_alive(), f"did not finish within {deadline}s"
    return box["value"]


#: A matrix Valhalla can produce and 2-opt cannot survive: every pair walkable
#: in both directions EXCEPT 0→6 (the reverse, 6→0, is fine). Valhalla snaps
#: each end of a pair on its own, so "reachable one way only" is a real shape,
#: and with the old boundary-only rule this exact matrix and order ran past
#: 200 000 passes without ever settling.
_ONE_WAY_HOLE = [
    [0.0, 4.0, 5.0, 7.0, 1.0, 4.0, float("inf")],
    [7.0, 0.0, 1.0, 5.0, 6.0, 3.0, 1.0],
    [5.0, 2.0, 0.0, 6.0, 1.0, 2.0, 7.0],
    [6.0, 7.0, 2.0, 0.0, 7.0, 9.0, 5.0],
    [5.0, 2.0, 8.0, 7.0, 0.0, 5.0, 6.0],
    [1.0, 1.0, 4.0, 8.0, 7.0, 0.0, 6.0],
    [5.0, 7.0, 6.0, 8.0, 3.0, 9.0, 0.0],
]


def test_two_opt_terminates_on_a_one_way_unreachable_pair():
    """It must settle, and must not leave the walk worse than it found it.

    The old rule accepted a reversal whenever the two boundary edges got
    cheaper. That is right for a symmetric matrix only; here an interior edge
    flips direction as well, so the accepted "improvement" was fictitious and
    the loop cycled forever — the request hung with no Valhalla call left to
    blame (the stack sat in this loop).
    """
    from agent.planner.optimize import _two_opt

    order = [5, 4, 6, 2, 1, 0, 3]
    before = cost_mod.walk_cost(order, _ONE_WAY_HOLE)
    after_order = _within(10.0, _two_opt, order, _ONE_WAY_HOLE)

    assert sorted(after_order) == sorted(order), "the order must stay a permutation"
    assert cost_mod.walk_cost(after_order, _ONE_WAY_HOLE) <= before + 1e-6
    assert order == [5, 4, 6, 2, 1, 0, 3], "the input list must not be mutated in place"


def test_two_opt_still_shortens_a_symmetric_route():
    """The real-cost check must not cost us the optimization itself."""
    from agent.planner.optimize import _two_opt

    n = 8
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            matrix[i][j] = matrix[j][i] = float(abs(i - j) * 10 + (i * j) % 7)
    order = [0, 7, 1, 6, 2, 5, 3, 4]  # deliberately zig-zag
    better = _within(10.0, _two_opt, order, matrix)

    assert sorted(better) == sorted(order)
    assert cost_mod.walk_cost(better, matrix) < cost_mod.walk_cost(order, matrix)

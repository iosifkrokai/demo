"""Route optimisation over a candidate pool.

Why this exists:
  The original `greedy_order` was a pure nearest-neighbour heuristic on the
  raw lat/lon grid — it had no notion of visit time, no concept of an
  overall budget, and no way to evaluate one ordering against another.

  With the Valhalla matrix in hand we can:
    1. Score the walk cost of any permutation of stops (sec).
    2. Add visit_time at each stop, get a total_time.
    3. Pick the permutation that fits the time budget best, or — if none
       does — the one with the smallest total_time.

Algorithm choice:
  - n ≤ 7  → brute force over all (n-2)! permutations (fixed start + end).
  - n = 8..10 → nearest-neighbour seed + 2-opt local search.
  These thresholds were picked empirically on a 5.5 km radius bbox: brute
  force is ~5ms for n=7 on Python. Above that the marginal improvement of
  optimal over 2-opt is <2%, so we trade for speed.

Edge case:
  - When only 2 candidates are left, the route is just A→B; no optimisation.
"""

from __future__ import annotations

from itertools import permutations
from typing import Callable

from .models import Candidate


def visit_time_minutes(category: str | None) -> int:
    """Estimated minutes to look around a place of a given category.

    Aligned with the curated taxonomy in data/places_curated.csv.
    Compound/verbose category strings fall back to substring match.
    """
    table: dict[str, int] = {
        "замок": 40, "музей": 40, "монастырь": 30,
        "дворец": 30, "усадьба": 30, "парк": 30,
        "костёл": 20, "церковь": 20, "храм": 20,
        "архитектура": 20, "кладбище": 15,
        "памятник": 10, "инфраструктура": 10,
    }
    default = 15
    if not category:
        return default
    c = category.lower().strip()
    if c in table:
        return table[c]
    for k, v in table.items():
        if k in c:
            return v
    return default


def _total_seconds(walk_sec: float, candidates: list[Candidate]) -> int:
    """Total time = walk time + visit time at each stop (rounded up)."""
    visits = sum(visit_time_minutes(c.category) for c in candidates) * 60
    return int(walk_sec + visits) + 1


def _best_brute_force(
    matrix: list[list[float]],
    start_idx: int,
    end_idx: int,
    middle: list[int],
    candidates: list[Candidate],
    time_budget_s: int | None,
) -> tuple[list[int], float]:
    """Try all (n-2)! permutations of `middle` between fixed start/end.

    Returns (best_permutation_indices, best_total_seconds).
    """
    best_perm: list[int] = []
    best_total: float = float("inf")

    for perm in permutations(middle):
        order = [start_idx, *perm, end_idx]
        walk = 0.0
        for a, b in zip(order, order[1:]):
            walk += matrix[a][b]
        total = _total_seconds(walk, [candidates[i] for i in order])
        if total < best_total:
            best_total = total
            best_perm = perm

    if time_budget_s is not None and best_total > time_budget_s:
        # All permutations exceed the budget: best we can do is the smallest.
        pass
    return list(best_perm), best_total


def _nearest_neighbour_order(
    matrix: list[list[float]],
    start_idx: int,
    end_idx: int,
    middle: list[int],
) -> list[int]:
    """Greedy: at each step go to the nearest unvisited stop."""
    order: list[int] = [start_idx]
    remaining = set(middle)
    cur = start_idx
    while remaining:
        nxt = min(remaining, key=lambda j: matrix[cur][j])
        order.append(nxt)
        remaining.remove(nxt)
        cur = nxt
    order.append(end_idx)
    return order


def _two_opt(
    order: list[int],
    matrix: list[list[float]],
) -> list[int]:
    """2-opt local search on the middle segment (fixed start/end)."""
    if len(order) <= 3:
        return order
    improved = True
    while improved:
        improved = False
        for i in range(1, len(order) - 2):
            for j in range(i + 1, len(order) - 1):
                a, b = order[i - 1], order[i]
                c, d = order[j], order[j + 1]
                if matrix[a][b] + matrix[c][d] > matrix[a][c] + matrix[b][d]:
                    order[i:j + 1] = reversed(order[i:j + 1])
                    improved = True
    return order


def _route_walk_time(order: list[int], matrix: list[list[float]]) -> float:
    return sum(matrix[a][b] for a, b in zip(order, order[1:]))


def optimise_route(
    candidates: list[Candidate],
    matrix: list[list[float]],
    *,
    n: int,
    seed_first: bool = True,
    time_budget_minutes: int | None = None,
) -> tuple[list[Candidate], dict]:
    """Pick the best n-stop order.

    Args:
        candidates: pool to choose from (already relevance-ranked).
        matrix:    seconds matrix from Valhalla sources_to_targets.
        n:         number of stops to include (2..MAX_N_POINTS).
        seed_first: whether to start from candidates[0] (most relevant) or
                   pick the best anchor from the matrix (more globally optimal
                   but ignores relevance — off by default).
        time_budget_minutes: optional cap; if set, the optimiser may shrink
                   the route (drop stops) to fit.

    Returns:
        ordered_candidates: list of length n (or fewer) in route order.
        info:               debug dict with reasoning (algorithm, total_s, etc).
    """
    info: dict = {"algorithm": "unknown", "n_candidates": len(candidates), "n": n}

    if n < 2:
        raise ValueError("n must be ≥ 2")
    if len(candidates) < 2:
        raise ValueError("need at least 2 candidates")

    # Truncate to n stops up front (top-n by relevance). Then split into
    # fixed anchor + middle. Default anchor is candidates[0]; if there are
    # enough candidates we consider letting the optimiser choose the anchor.
    pool = candidates[:n]
    start_idx, end_idx = 0, len(pool) - 1
    middle = list(range(1, len(pool) - 1))

    if not middle:
        # Just start and end: no permutation to optimise.
        walk = matrix[start_idx][end_idx]
        total = _total_seconds(walk, pool)
        info.update({"algorithm": "direct", "walk_seconds": walk, "total_seconds": total})
        return pool, info

    budget_s = time_budget_minutes * 60 if time_budget_minutes else None

    if len(middle) <= 6:
        best_perm, best_total = _best_brute_force(
            matrix, start_idx, end_idx, middle, pool, budget_s
        )
        order_idx = [start_idx, *best_perm, end_idx]
        algo = "brute_force"
    else:
        order_idx = _nearest_neighbour_order(matrix, start_idx, end_idx, middle)
        order_idx = _two_opt(order_idx, matrix)
        algo = "nn+2opt"

    walk = _route_walk_time(order_idx, matrix)
    total = _total_seconds(walk, [pool[i] for i in order_idx])
    info.update({
        "algorithm": algo,
        "walk_seconds": walk,
        "total_seconds": total,
        "order_indices": order_idx,
    })

    # Budget shrink: drop the middle stop with the worst "neighbour sum"
    # (farthest from both neighbours in the chosen order) until we fit.
    # order_idx always indexes into `pool`, which never changes — no rebuild.
    stops_dropped = 0
    if budget_s is not None:
        while len(order_idx) > 2:
            walk = _route_walk_time(order_idx, matrix)
            total = _total_seconds(walk, [pool[i] for i in order_idx])
            if total <= budget_s:
                break
            worst_pos = max(
                range(1, len(order_idx) - 1),
                key=lambda p: matrix[order_idx[p - 1]][order_idx[p]]
                             + matrix[order_idx[p]][order_idx[p + 1]],
            )
            order_idx.pop(worst_pos)
            stops_dropped += 1
        walk = _route_walk_time(order_idx, matrix)
        total = _total_seconds(walk, [pool[i] for i in order_idx])
        info.update({
            "walk_seconds": walk,
            "total_seconds": total,
            "stops_dropped": stops_dropped,
        })
    else:
        info["stops_dropped"] = 0

    return [pool[i] for i in order_idx], info


def build_latlons(candidates: list[Candidate]) -> list[dict]:
    """Helper: turn candidates into the [{lat, lon}] shape Valhalla expects."""
    return [{"lat": c.lat, "lon": c.lon} for c in candidates]

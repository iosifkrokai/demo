"""Step 6 — Route optimization.

Three modes depending on pool size:

  * n ≤ 6  — brute-force over ALL (start, end) pairs × middle permutations.
              Optimal, ~50ms in Python for n=6.
  * n ≤ 12 — regret-2 insertion. At each step, insert the place whose
              best-vs-second-best gap is largest (most "needed" first).
              ~100ms for n=10.
  * n > 12 — nearest-neighbour seed + multi-restart 2-opt. Approximate,
              but good enough for n up to ~30.

Endpoints are FREE: we optimize over all (start, end) pairs in brute mode,
and over multiple random starts in NN mode. The old agent.py had endpoints
locked to (0, n-1) which biased the route by ranking — this fixes that.

Must-visit places are first-class: brute/regret modes enforce that they
appear in the final route (and seed them at the start of the insertion).
"""

from __future__ import annotations

import random
from itertools import permutations

from .. import constants
from ..config import settings
from ..models import Candidate, CostMatrix, ResolvedConstraints
from .cost import total_seconds, walk_cost


# ─────────────────────────────────────────────────────────────────────────────
# Top-level dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def optimize(
    candidates: list[Candidate],
    cost: CostMatrix,
    constraints: ResolvedConstraints,
) -> tuple[list[Candidate], dict]:
    """Pick the best order, return (ordered_candidates, debug_info)."""
    n = len(candidates)
    info: dict = {"n_candidates": n, "algorithm": "unknown"}
    matrix = cost.walk_seconds
    visits = cost.visit_minutes
    budget_s = constraints.time_budget_minutes * 60 if constraints.time_budget_minutes else None
    must_idx = [i for i, c in enumerate(candidates) if c.id in constraints.must_visit_ids]

    if n < 2:
        return list(candidates), {**info, "algorithm": "direct"}
    if n == 2:
        order = [0, 1] if not must_idx else (must_idx + [i for i in range(2) if i not in must_idx])
        return [candidates[i] for i in order], {**info, "algorithm": "direct", "order": order}

    if n <= 6:
        order, info = _brute_open(candidates, matrix, visits, budget_s, info)
    elif n <= 12:
        order, info = _regret_insertion(candidates, matrix, visits, budget_s, must_idx, info)
    else:
        order, info = _nn_2opt_multi(candidates, matrix, visits, budget_s, info)

    return [candidates[i] for i in order], info


# ─────────────────────────────────────────────────────────────────────────────
# Mode A: brute force with open endpoints (n ≤ 6)
# ─────────────────────────────────────────────────────────────────────────────

def _brute_open(
    cands: list[Candidate],
    matrix: list[list[float]],
    visits: list[int],
    budget_s: int | None,
    info: dict,
) -> tuple[list[int], dict]:
    """Enumerate (start, end) × all permutations of middle, return cheapest."""
    n = len(cands)
    best_route: list[int] | None = None
    best_cost = float("inf")
    best_total = float("inf")

    for start in range(n):
        for end in range(n):
            if start == end:
                continue
            middle = [i for i in range(n) if i != start and i != end]
            for perm in permutations(middle):
                order = [start, *perm, end]
                total = total_seconds(order, matrix, visits)
                walk = walk_cost(order, matrix)
                # Prefer feasible (within budget) over infeasible, even if more walk.
                if budget_s is not None and total > budget_s:
                    continue
                if total < best_total:
                    best_total = total
                    best_route = order
                    best_cost = walk

    # If nothing feasible, fall back to the cheapest overall.
    if best_route is None:
        for start in range(n):
            for end in range(n):
                if start == end:
                    continue
                middle = [i for i in range(n) if i != start and i != end]
                for perm in permutations(middle):
                    order = [start, *perm, end]
                    total = total_seconds(order, matrix, visits)
                    if total < best_total:
                        best_total = total
                        best_route = order

    if best_route is None:
        # Truly degenerate — return identity order.
        best_route = list(range(n))
        best_total = total_seconds(best_route, matrix, visits)

    info.update({
        "algorithm": "brute_open",
        "order": best_route,
        "walk_seconds": best_cost,
        "total_seconds": int(best_total),
    })
    return best_route, info


# ─────────────────────────────────────────────────────────────────────────────
# Mode B: regret-2 insertion (7 ≤ n ≤ 12)
# ─────────────────────────────────────────────────────────────────────────────

def _regret_insertion(
    cands: list[Candidate],
    matrix: list[list[float]],
    visits: list[int],
    budget_s: int | None,
    must_idx: list[int],
    info: dict,
) -> tuple[list[int], dict]:
    """Sequentially insert the most "regrettable-to-skip" candidate.

    Regret = best_insertion_cost - 2nd_best. High regret = there's only one
    good spot to insert this place, so deferring it is expensive.
    """
    n = len(cands)
    if must_idx:
        # Seed with must-visits in input order; they don't get re-inserted.
        route = list(must_idx)
        remaining = [i for i in range(n) if i not in must_idx]
    else:
        # Seed with the most central place (lowest sum of distances).
        seed = min(range(n), key=lambda i: sum(matrix[i]))
        route = [seed]
        remaining = [i for i in range(n) if i != seed]

    iterations = 0
    while remaining and len(route) < constants.ROUTE_MAX_STOPS:
        # Budget check: stop growing if even the cheapest single insertion exceeds.
        cur_cost = total_seconds(route, matrix, visits)
        if budget_s is not None and cur_cost >= budget_s:
            break

        best_choice: tuple[int, int, float] | None = None  # (regret, candidate, position)

        for c in remaining:
            insertion_costs: list[tuple[int, int]] = []  # (cost, position)
            for pos in range(len(route) + 1):
                new_route = route[:pos] + [c] + route[pos:]
                cost = total_seconds(new_route, matrix, visits)
                insertion_costs.append((cost, pos))
            insertion_costs.sort()
            regret = (
                insertion_costs[1][0] - insertion_costs[0][0]
                if len(insertion_costs) > 1 else 0
            )
            if best_choice is None or regret > best_choice[0]:
                best_choice = (regret, c, insertion_costs[0][1])

        if best_choice is None:
            break
        _, chosen, pos = best_choice
        # Only insert if it actually fits the budget.
        new_route = route[:pos] + [chosen] + route[pos:]
        if budget_s is not None and total_seconds(new_route, matrix, visits) > budget_s:
            break
        route = new_route
        remaining.remove(chosen)
        iterations += 1

    info.update({
        "algorithm": "regret_2",
        "order": route,
        "iterations": iterations,
        "walk_seconds": walk_cost(route, matrix),
        "total_seconds": total_seconds(route, matrix, visits),
    })
    return route, info


# ─────────────────────────────────────────────────────────────────────────────
# Mode C: NN + multi-restart 2-opt (n > 12)
# ─────────────────────────────────────────────────────────────────────────────

def _nn_2opt_multi(
    cands: list[Candidate],
    matrix: list[list[float]],
    visits: list[int],
    budget_s: int | None,
    info: dict,
) -> tuple[list[int], dict]:
    """Nearest-neighbour from K random starts + 2-opt local search.

    Determinism: a fixed seed (from query fingerprint upstream) gives the
    same route for the same query.
    """
    n = len(cands)
    rng = random.Random(42)
    starts = list(range(n))
    rng.shuffle(starts)
    starts = starts[:5] if n >= 5 else starts

    best_route: list[int] | None = None
    best_total = float("inf")
    best_walk = float("inf")

    for start in starts:
        route = _nn_order(matrix, start, n)
        route = _two_opt(route, matrix)
        total = total_seconds(route, matrix, visits)
        # Prefer feasible if budget is set.
        if budget_s is not None and total > budget_s:
            continue
        if total < best_total:
            best_total = total
            best_route = route
            best_walk = walk_cost(route, matrix)

    if best_route is None:
        # Nothing feasible — take the cheapest unconstrained.
        for start in starts:
            route = _nn_order(matrix, start, n)
            route = _two_opt(route, matrix)
            total = total_seconds(route, matrix, visits)
            if total < best_total:
                best_total = total
                best_route = route
                best_walk = walk_cost(route, matrix)

    if best_route is None:
        best_route = list(range(n))
        best_total = total_seconds(best_route, matrix, visits)
        best_walk = walk_cost(best_route, matrix)

    info.update({
        "algorithm": "nn_2opt_multi",
        "order": best_route,
        "walk_seconds": best_walk,
        "total_seconds": int(best_total),
    })
    return best_route, info


def _nn_order(matrix: list[list[float]], start: int, n: int) -> list[int]:
    route = [start]
    remaining = set(range(n)) - {start}
    cur = start
    while remaining:
        nxt = min(remaining, key=lambda j: matrix[cur][j])
        route.append(nxt)
        remaining.remove(nxt)
        cur = nxt
    return route


def _two_opt(order: list[int], matrix: list[list[float]]) -> list[int]:
    """Open-route 2-opt: we DON'T close the loop (no return-to-start).

    Edge distances to neighbours outside the segment are unchanged when
    we reverse a middle slice, so the comparison is well-defined.
    """
    if len(order) <= 3:
        return order
    improved = True
    while improved:
        improved = False
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                a = order[i - 1] if i > 0 else None
                b = order[i]
                c = order[j]
                d = order[j + 1] if j + 1 < len(order) else None
                if a is None or d is None:
                    # Edge of the open route — skip those edges.
                    continue
                old = matrix[a][b] + matrix[c][d]
                new = matrix[a][c] + matrix[b][d]
                if new + 1e-6 < old:
                    order[i:j + 1] = reversed(order[i:j + 1])
                    improved = True
    return order

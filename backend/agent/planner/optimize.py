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

Budget-constrained greedy selection (Defect 3 fix):
  After optimizing, _budget_constrain greedily removes stops whose addition
  would exceed the time budget or violate the max-leg walkability constraint.
  This prevents the pipeline from returning 10–28 h walks for 2-hour queries.
"""

from __future__ import annotations

import random
from itertools import permutations

from contracts.planner import Candidate, CostMatrix, ResolvedConstraints
from domain import constants

from .cost import total_seconds, walk_cost

# km/h pedestrian speed — used to convert walk time to distance for the
# max-leg check (avoids calling Valhalla just for a distance estimate).
WALK_KMH = 4.0
# Fallback per-leg ceiling for a driven tour (4 h) when the query sets no budget.
_DRIVE_LEG_FALLBACK_S = 4 * 3600


# Top-level dispatcher

def optimize(
    candidates: list[Candidate],
    cost: CostMatrix,
    constraints: ResolvedConstraints,
    costing: str = "pedestrian",
) -> tuple[list[Candidate], dict]:
    """Pick the best order, return (ordered_candidates, debug_info)."""
    n = len(candidates)
    info: dict = {"n_candidates": n, "algorithm": "unknown"}
    matrix = cost.walk_seconds
    visits = cost.visit_minutes
    budget_s = constraints.time_budget_minutes * 60 if constraints.time_budget_minutes else None
    must_idx = [i for i, c in enumerate(candidates) if c.id in constraints.must_visit_ids]

    if n < 2:
        return list(candidates), _report_missing_must(
            {**info, "algorithm": "direct", "order": list(range(n))},
            list(range(n)),
            candidates,
            constraints,
        )
    if n == 2:
        order = [0, 1] if not must_idx else (must_idx + [i for i in range(2) if i not in must_idx])
        return [candidates[i] for i in order], _report_missing_must(
            {**info, "algorithm": "direct", "order": order},
            order,
            candidates,
            constraints,
        )

    if n <= 6:
        order, info = _brute_open(
            candidates, matrix, visits, budget_s, info, costing=costing
        )
    elif n <= 12:
        order, info = _regret_insertion(
            candidates, matrix, visits, budget_s, must_idx, info, costing=costing
        )
    else:
        order, info = _nn_2opt_multi(
            candidates, matrix, visits, budget_s, info, costing=costing
        )

    # Defect 3 fix: budget-constrained greedy selection
    # After optimization, greedily trim the route to fit inside the budget
    # and respect the max-leg walkability constraint.  Must-visit places are
    # protected; all other stops are dropped in reverse-relevance order when
    # the budget is exceeded.
    must_ids = set(constraints.must_visit_ids)
    constrained, dropped = _budget_constrain(
        candidates, order, matrix, visits, budget_s, must_ids, costing=costing
    )
    if constrained != list(range(n)):
        new_order = [i for i in constrained if i < len(candidates)]
        # The count alone cannot answer «почему этой остановки нет в маршруте»,
        # so the names travel with it — in the order they would have been
        # visited, which is the order the traveller would have experienced them.
        kept = set(new_order)
        dropped_names = [candidates[i].name for i in order if i not in kept]
        info = {
            **info,
            "stops_dropped": dropped,
            "stops_dropped_names": dropped_names,
            "order": new_order,
        }
        order = new_order

    info = _report_missing_must(info, order, candidates, constraints)
    return [candidates[i] for i in order], info


def _report_missing_must(
    info: dict,
    order: list[int],
    candidates: list[Candidate],
    constraints: ResolvedConstraints,
) -> dict:
    """Record must-visit ids absent from the final route instead of losing them.

    ``_budget_constrain`` protects must-visits, but a mandatory id may never
    have reached the pool (dropped upstream as unreachable or outside the
    area). Returning a route that quietly omits it is the defect; we surface
    ``missing_must_visit_ids`` in ``info`` so the verifier can mark the
    requirement unmet/infeasible.
    """
    if not constraints.must_visit_ids:
        return info
    final_ids = {candidates[i].id for i in order if 0 <= i < len(candidates)}
    missing = [
        mid
        for mid in dict.fromkeys(constraints.must_visit_ids)
        if mid not in final_ids
    ]
    if missing:
        return {**info, "missing_must_visit_ids": missing}
    return info


def _budget_constrain(
    candidates: list[Candidate],
    order: list[int],
    matrix: list[list[float]],
    visits: list[int],
    budget_s: int | None,
    must_ids: set[int],
    *,
    costing: str = "pedestrian",
) -> tuple[list[int], int]:
    """Greedily trim `order` to fit inside the time budget and max-leg constraint.

    Must-visit places are protected.  Other stops are considered for removal in
    order of increasing relevance (least-relevant first), until both constraints
    are satisfied or only must-visits remain.

    Returns (trimmed_order, n_dropped).
    """
    if not order:
        return order, 0

    route = list(order)
    must_idx_set = {i for i in route if candidates[i].id in must_ids}
    dropped = 0

    # Compute the maximum allowed time for a single leg (walkability rule for a
    # pedestrian tour; the budget itself when the tour is driven).
    max_leg_s = _max_leg_seconds(budget_s, costing)

    # Greedy removal loop.
    while len(route) > 1:
        total = total_seconds(route, matrix, visits)
        max_leg = _max_leg(route, matrix)

        # Stop if both constraints are satisfied.
        budget_ok = budget_s is None or total <= budget_s
        leg_ok = max_leg <= max_leg_s
        if budget_ok and leg_ok:
            break

        # Find the least-relevant removable stop (prefer to drop non-must-visits).
        removable = [i for i in route if i not in must_idx_set]
        if not removable:
            break  # only must-visits remain — can't trim further

        # Drop the least-relevant stop.
        removable.sort(key=lambda i: candidates[i].relevance)
        to_remove = removable[0]
        route.remove(to_remove)
        dropped += 1

    return route, dropped


def _max_leg_seconds(budget_s: int | None, costing: str = "pedestrian") -> float:
    """Maximum allowed time for a single leg, in seconds.

    The cap is a *walkability* rule: a pedestrian tour must not contain a leg
    nobody would walk. A region-wide tour is driven (costing "auto"), where a
    long leg is normal, so the budget itself is the only bound.
    """
    if costing != "pedestrian":
        return float(budget_s) if budget_s else _DRIVE_LEG_FALLBACK_S
    if budget_s is None:
        # No budget → use the absolute max in km converted to seconds.
        return (constants.MAX_WALK_LEG_KM / WALK_KMH) * 3600
    budget_share_s = budget_s * constants.WALK_LEG_BUDGET_SHARE
    km_limit_s = (constants.MAX_WALK_LEG_KM / WALK_KMH) * 3600
    return min(budget_share_s, km_limit_s)


def _max_leg(route: list[int], matrix: list[list[float]]) -> float:
    """Longest single walking leg in a route, in seconds."""
    if len(route) < 2:
        return 0.0
    return max(matrix[a][b] for a, b in zip(route, route[1:]))


# Mode A: brute force with open endpoints (n ≤ 6)

def _brute_open(
    cands: list[Candidate],
    matrix: list[list[float]],
    visits: list[int],
    budget_s: int | None,
    info: dict,
    *,
    costing: str = "pedestrian",
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


# Mode B: regret-2 insertion (7 ≤ n ≤ 12)

def _regret_insertion(
    cands: list[Candidate],
    matrix: list[list[float]],
    visits: list[int],
    budget_s: int | None,
    must_idx: list[int],
    info: dict,
    *,
    costing: str = "pedestrian",
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
        # Seed with the most relevant place (= the query's best match), not the
        # most "central" one: with a pool that mixes the target town with far
        # outliers, the central seed lands in the outlier cluster and every
        # walkable insertion is then rejected, collapsing the route to 1 stop.
        seed = max(range(n), key=lambda i: cands[i].relevance)
        route = [seed]
        remaining = [i for i in range(n) if i != seed]

    iterations = 0
    skipped = 0
    max_leg_s = _max_leg_seconds(budget_s, costing=costing)
    while remaining:
        # Budget check: stop growing if even the cheapest single insertion exceeds.
        cur_cost = total_seconds(route, matrix, visits)
        if budget_s is not None and cur_cost >= budget_s:
            break

        best_choice: tuple[int, int, float] | None = None  # (regret, candidate, position)

        for c in remaining:
            insertion_costs: list[tuple[int, int]] = []  # (cost, position)
            for pos in range(len(route) + 1):
                new_route = route[:pos] + [c] + route[pos:]
                # Skip positions that make a single leg unwalkable: inserting
                # first and repairing later means _budget_constrain deletes
                # stops down to a 1-stop route.
                if _max_leg(new_route, matrix) > max_leg_s:
                    continue
                cost = total_seconds(new_route, matrix, visits)
                insertion_costs.append((cost, pos))
            if not insertion_costs:
                continue  # this candidate cannot be walked from anywhere yet
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
            # This candidate does not fit right now — usually a place far
            # outside the walkable cluster, picked first because regret ranks
            # by insertion-gap rather than by cost.  Drop it from consideration
            # and keep trying the rest; breaking here truncated every route to
            # its seed (2 stops for a 3-hour budget).
            remaining.remove(chosen)
            skipped += 1
            continue
        route = new_route
        remaining.remove(chosen)
        iterations += 1

    info.update({
        "algorithm": "regret_2",
        "order": route,
        "iterations": iterations,
        "skipped_over_budget": skipped,
        "walk_seconds": walk_cost(route, matrix),
        "total_seconds": total_seconds(route, matrix, visits),
    })
    return route, info


# Mode C: NN + multi-restart 2-opt (n > 12)

def _nn_2opt_multi(
    cands: list[Candidate],
    matrix: list[list[float]],
    visits: list[int],
    budget_s: int | None,
    info: dict,
    *,
    costing: str = "pedestrian",
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

    A reversal is accepted only when it lowers the order's REAL cost — the sum
    `walk_cost` measures — not merely the two boundary edges. The boundary sum
    alone is right for a symmetric matrix, and Valhalla's is not one: it snaps
    each end on its own and can connect a pair one way only, so
    `matrix[a][c] + matrix[b][d] < matrix[a][b] + matrix[c][d]` may hold while
    reversing the slice makes the walk longer (every interior edge flips
    direction too). Trusting that sum made the search cycle forever on a pair
    Valhalla could not connect one way: a request hung past every timeout, with
    no Valhalla call left to blame and the stack parked in this loop. Requiring a
    strict decrease of the real cost bounds it — the cost can only fall, and
    there are finitely many orders.
    """
    if len(order) <= 3:
        return order
    n = len(order)
    current = walk_cost(order, matrix)
    improved = True
    while improved:
        improved = False
        for i in range(n - 1):
            for j in range(i + 1, n):
                a = order[i - 1] if i > 0 else None
                b = order[i]
                c = order[j]
                d = order[j + 1] if j + 1 < n else None
                if a is None or d is None:
                    # Edge of the open route — skip those edges.
                    continue
                old = matrix[a][b] + matrix[c][d]
                new = matrix[a][c] + matrix[b][d]
                # False for a non-finite cell too, so an unreachable pair is
                # never "improved" into the order.
                if not new + 1e-6 < old:
                    continue
                candidate = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
                candidate_cost = walk_cost(candidate, matrix)
                if candidate_cost + 1e-6 < current:
                    order = candidate
                    current = candidate_cost
                    improved = True
    return order

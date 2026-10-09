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

import logging
import math
import random
from itertools import permutations
from typing import Any

from contracts.planner import Candidate, CostMatrix, ResolvedConstraints
from core.errors import NoRoutePossible, UpstreamUnavailable
from domain import constants
from infra.valhalla_client import optimized_route as valhalla_optimized_route

from .cost import prune_unroutable_stops, total_seconds, visit_cost, walk_cost

log = logging.getLogger(__name__)

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
            candidates, matrix, visits, budget_s, info,
            costing=costing, round_trip=constraints.round_trip,
        )
    elif n <= 12:
        order, info = _regret_insertion(
            candidates, matrix, visits, budget_s, must_idx, info,
            costing=costing, round_trip=constraints.round_trip,
        )
    else:
        order, info = _nn_2opt_multi(
            candidates, matrix, visits, budget_s, info,
            costing=costing, round_trip=constraints.round_trip,
        )

    # Defect 3 fix: budget-constrained greedy selection
    # After optimization, greedily trim the route to fit inside the budget
    # and respect the max-leg walkability constraint.  Must-visit places are
    # protected; trims are chosen by cost (see _budget_constrain).
    must_ids = set(constraints.must_visit_ids)
    constrained, dropped = _budget_constrain(
        candidates, order, matrix, visits, budget_s, must_ids,
        costing=costing, round_trip=constraints.round_trip,
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


def _leg_value(cell: float) -> float:
    """A single leg's contribution to a feasibility total.

    UNKNOWN_S (NaN) contributes 0 (we could not ask; do not invent a leg), an
    infinite/sentinel cell contributes UNREACHABLE_S so the order stays
    infeasible, everything else is the real duration.
    """
    if math.isnan(cell):
        return 0.0
    if not math.isfinite(cell):
        return float(constants.UNREACHABLE_S)
    return cell


def _route_legs(order: list[int], round_trip: bool) -> list[tuple[int, int]]:
    """The legs of a route, including the return leg for a round trip."""
    legs = list(zip(order, order[1:]))
    if round_trip and len(order) >= 2:
        legs.append((order[-1], order[0]))
    return legs


def _order_total(
    order: list[int],
    matrix: list[list[float]],
    visits: list[int],
    round_trip: bool = False,
) -> float:
    """Total route time (walk + visit) with the return leg when it is a loop.

    The optimizer used to price a round trip as an open walk while the verifier
    charged the walk home, so a 60-minute loop came back ``fits:false`` after the
    trim believed it fit. Both now use the same arithmetic.
    """
    total = total_seconds(order, matrix, visits)
    if round_trip and len(order) >= 2:
        total += _leg_value(matrix[order[-1]][order[0]])
    return total


def _measured_total(
    order: list[int],
    matrix: list[list[float]],
    visits: list[int],
    round_trip: bool,
) -> float:
    """Budget total over the MEASURED legs only.

    An unreachable/sentinel leg is not a real duration, so it is not summed into
    the budget: the leg constraint handles it separately. Summing 1e9 here made
    the budget look violated and cost the route four innocent stops.
    """
    total = float(visit_cost(order, visits))
    for a, b in _route_legs(order, round_trip):
        cell = matrix[a][b]
        if math.isfinite(cell):
            total += cell
    return total


def _worst_leg(
    order: list[int], matrix: list[list[float]], round_trip: bool
) -> tuple[int, int] | None:
    """The longest real leg of the route (NaN/"could not ask" legs skipped)."""
    worst: tuple[int, int] | None = None
    for a, b in _route_legs(order, round_trip):
        cell = matrix[a][b]
        if math.isnan(cell):
            continue
        if worst is None or cell > matrix[worst[0]][worst[1]]:
            worst = (a, b)
    return worst


def _most_saving(
    removable: list[int], route: list[int], total_of
) -> int:
    """The removable stop whose deletion buys the most time."""
    current = total_of(route)
    best_i, best_saving = removable[0], float("-inf")
    for x in removable:
        saving = current - total_of([i for i in route if i != x])
        if saving > best_saving:
            best_saving, best_i = saving, x
    return best_i


def _budget_constrain(
    candidates: list[Candidate],
    order: list[int],
    matrix: list[list[float]],
    visits: list[int],
    budget_s: int | None,
    must_ids: set[int],
    *,
    costing: str = "pedestrian",
    round_trip: bool = False,
) -> tuple[list[int], int]:
    """Greedily trim `order` to fit inside the time budget and max-leg constraint.

    Must-visit places are protected. What gets dropped is chosen by COST, not by
    relevance: an unroutable or over-long leg is fixed by dropping the stop at
    one of its ends, and a budget overrun is fixed by dropping the stop whose
    removal buys the most time (its visit time plus the extra leg it forces).
    The old rule dropped the least-``relevance`` stop, and relevance (a raw RRF
    score, near-flat at 0.033 vs 0.029) has nothing to do with cost — a live
    region query kept a 150 km outlier while dropping the four stops next to the
    anchor. After a trim the survivors are re-ordered, because a subsequence can
    zig-zag.

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

    def total_of(r: list[int]) -> float:
        return _measured_total(r, matrix, visits, round_trip)

    # Greedy removal loop.
    while len(route) > 1:
        leg = _worst_leg(route, matrix, round_trip)
        leg_ok = leg is None or matrix[leg[0]][leg[1]] <= max_leg_s
        budget_ok = budget_s is None or total_of(route) <= budget_s
        if budget_ok and leg_ok:
            break

        removable = [i for i in route if i not in must_idx_set]
        if not removable:
            break  # only must-visits remain — can't trim further

        if not leg_ok and leg is not None:
            # The LEG is the problem: drop the stop that causes it, not a
            # relevance-ranked bystander. A sentinel leg must not make us delete
            # unrelated stops one by one.
            endpoints = [x for x in leg if x in removable]
            if endpoints:
                to_remove = _most_saving(endpoints, route, total_of)
                route.remove(to_remove)
                dropped += 1
                continue
            # Both ends are must-visits: the leg cannot be removed by dropping.
            # Only keep trimming if the budget is also genuinely violated.
            if budget_ok:
                break

        to_remove = _most_saving(removable, route, total_of)
        route.remove(to_remove)
        dropped += 1

    # Re-optimise the order of the survivors: the trimmed route is a subsequence
    # of the optimized one and can zig-zag (drop stop 3, stops 1 and 4 may now be
    # adjacent in the wrong direction). 2-opt only ever decreases the real cost.
    if dropped:
        route = _two_opt(route, matrix)

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


def _max_leg(
    route: list[int], matrix: list[list[float]], round_trip: bool = False
) -> float:
    """Longest single walking leg in a route, in seconds.

    A round trip's return leg counts too, and a NaN ("could not ask") leg is
    skipped — it is not evidence of an over-long walk.
    """
    best = 0.0
    for a, b in _route_legs(route, round_trip):
        cell = matrix[a][b]
        if math.isnan(cell):
            continue
        best = max(best, cell)
    return best


# Mode A: brute force with open endpoints (n ≤ 6)

def _brute_open(
    cands: list[Candidate],
    matrix: list[list[float]],
    visits: list[int],
    budget_s: int | None,
    info: dict,
    *,
    costing: str = "pedestrian",
    round_trip: bool = False,
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
                total = _order_total(order, matrix, visits, round_trip)
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
                    total = _order_total(order, matrix, visits, round_trip)
                    if total < best_total:
                        best_total = total
                        best_route = order

    if best_route is None:
        # Truly degenerate — return identity order.
        best_route = list(range(n))
        best_total = _order_total(best_route, matrix, visits, round_trip)

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
    round_trip: bool = False,
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
        cur_cost = _order_total(route, matrix, visits, round_trip)
        if budget_s is not None and cur_cost >= budget_s:
            break

        best_choice: tuple[float, int, int] | None = None  # (regret, candidate, position)

        for c in remaining:
            insertion_costs: list[tuple[float, int]] = []  # (cost, position)
            for pos in range(len(route) + 1):
                new_route = route[:pos] + [c] + route[pos:]
                # Skip positions that make a single leg unwalkable: inserting
                # first and repairing later means _budget_constrain deletes
                # stops down to a 1-stop route.
                if _max_leg(new_route, matrix, round_trip) > max_leg_s:
                    continue
                cost = _order_total(new_route, matrix, visits, round_trip)
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
        if budget_s is not None and _order_total(
            new_route, matrix, visits, round_trip
        ) > budget_s:
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
        "total_seconds": int(_order_total(route, matrix, visits, round_trip)),
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
    round_trip: bool = False,
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
        total = _order_total(route, matrix, visits, round_trip)
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
            total = _order_total(route, matrix, visits, round_trip)
            if total < best_total:
                best_total = total
                best_route = route
                best_walk = walk_cost(route, matrix)

    if best_route is None:
        best_route = list(range(n))
        best_total = _order_total(best_route, matrix, visits, round_trip)
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


def _valhalla_order(
    route: list[Candidate],
    info: dict,
    *,
    costing: str,
    timeout: float | None = None,
    retries: int | None = None,
) -> tuple[list[Candidate], dict]:
    """Let Valhalla order the walk when nothing pins the sequence.

    With no GPS start and no must-visit stops, /optimized_route is the router's
    own solver, so the order the tourist sees is the one Valhalla built. With a
    fixed start or must-visit stops the planned order wins and Valhalla only
    draws it.

    The call is optional: over a region-wide tour the solver does not answer at
    all, so the caller passes a short single-shot budget and the planned order
    stands when it is exceeded (UpstreamUnavailable).
    """
    if len(route) < 3:
        return route, info
    coords = [{"lat": c.lat, "lon": c.lon} for c in route]
    try:
        v_order, _, _ = valhalla_optimized_route(
            coords, costing=costing, timeout=timeout, retries=retries
        )
    except UpstreamUnavailable:
        return route, info
    if len(v_order) != len(route) or sorted(v_order) != list(range(len(route))):
        return route, info
    planned = list(info.get("order") or range(len(route)))
    return (
        [route[i] for i in v_order],
        {
            **info,
            "order": [planned[i] for i in v_order],
            "algorithm": f"{info.get('algorithm')}+valhalla",
        },
    )


def _prune_unroutable(
    route: list[Candidate],
    candidates: list[Candidate],
    cost: CostMatrix,
    must_visit_ids: list[int] | None = None,
) -> tuple[list[Candidate], list[Any]]:
    """Drop stops the matrix cannot connect to their predecessor in this order.

    Valhalla's own verdict (UNREACHABLE_S = 400 "No path could be found for
    input"): a chapel on a road island, reachable from its neighbour but from
    nothing else, made /route fail for the WHOLE tour — the UI showed every
    point drawn with no line between them.

    Returns ``(route, report)`` where ``report`` carries a machine reason per
    flagged stop.  A MANDATORY stop is never removed: it stays on the route and
    is reported as ``must_visit_unroutable``, which the caller records in the
    plan trace so the verifier can return ``unmet``/``infeasible`` instead of a
    route that quietly lost the place the tourist demanded.
    """
    route, pruned = prune_unroutable_stops(
        route, candidates, cost, must_visit_ids=must_visit_ids
    )
    if pruned:
        log.warning(
            "pruned %d stop(s) Valhalla cannot reach in this order: %s",
            len(pruned),
            ", ".join(c.name for c in pruned),
        )
    if len(route) < 2:
        raise NoRoutePossible(
            "Valhalla не нашла дороги между нашими точками — "
            "уточните город или район"
        )
    return route, pruned


def _order_after_prune(
    info: dict, route: list[Candidate], candidates: list[Candidate]
) -> dict:
    """Re-point ``info["order"]`` at the stops that survived a prune.

    ``info["order"]`` holds indices into the cost matrix, and validate prices the
    walk and the visits through them. prune_unroutable_stops shortens ``route``
    without touching the order, so validate saw a length mismatch, fell back to
    ``range(n)`` and summed the *first n candidates*' legs instead of the
    survivors' — reporting a time budget for a walk nobody takes. Recomputing the
    indices from the surviving route keeps the totals aligned with what is drawn.
    """
    order = info.get("order") or []
    if len(order) == len(route):
        return info
    position = {id(c): i for i, c in enumerate(candidates)}
    return {**info, "order": [position[id(c)] for c in route]}

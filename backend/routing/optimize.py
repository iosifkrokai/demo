"""Step 6 — Route optimization."""

from __future__ import annotations

import logging
import math
import random
from collections.abc import Callable
from itertools import pairwise, permutations
from typing import Any

from core import constants
from core.errors import NoRoutePossibleError, UpstreamUnavailableError
from routing.models import Candidate, CostMatrix, OriginAnchor, ResolvedConstraints
from routing.valhalla.constants import costing_for
from routing.valhalla.route import optimized_route as valhalla_optimized_route

from .cost import (
    ORIGIN_INDEX,
    leg_seconds,
    prune_unroutable_stops,
    route_legs,
    total_seconds,
    visit_cost,
    walk_cost,
)

log = logging.getLogger(__name__)

BRUTE_FORCE_MAX = 6
REGRET_INSERTION_MAX = 12


NN_STARTS = 5


def optimize(
    candidates: list[Candidate],
    cost: CostMatrix,
    constraints: ResolvedConstraints,
    costing: str = "pedestrian",
) -> tuple[list[Candidate], dict[str, Any]]:
    """Pick the best order, return (ordered_candidates, debug_info)."""
    n = len(candidates)
    info: dict[str, Any] = {"n_candidates": n, "algorithm": "unknown"}
    matrix = cost.walk_seconds
    visits = cost.visit_minutes
    anchor = cost.anchor
    budget_s = constraints.time_budget_minutes * 60 if constraints.time_budget_minutes else None
    must_idx = [i for i, c in enumerate(candidates) if c.id in constraints.must_visit_ids]

    if n < constants.MIN_LEG_POINTS:
        return list(candidates), _report_missing_must(
            {**info, "algorithm": "direct", "order": list(range(n))},
            list(range(n)),
            candidates,
            constraints,
        )
    if n == constants.MIN_LEG_POINTS:
        order = [0, 1] if not must_idx else (must_idx + [i for i in range(2) if i not in must_idx])
        if anchor is not None:
            flipped = order[::-1]
            if _order_total(flipped, matrix, visits, constraints.round_trip, anchor) < _order_total(
                order, matrix, visits, constraints.round_trip, anchor
            ):
                order = flipped
        return [candidates[i] for i in order], _report_missing_must(
            {**info, "algorithm": "direct", "order": order},
            order,
            candidates,
            constraints,
        )

    if n <= BRUTE_FORCE_MAX:
        order, info = _brute_open(
            candidates,
            matrix,
            visits,
            budget_s,
            info,
            round_trip=constraints.round_trip,
            anchor=anchor,
        )
    elif n <= REGRET_INSERTION_MAX:
        order, info = _regret_insertion(
            candidates,
            matrix,
            visits,
            budget_s,
            must_idx,
            info=info,
            costing=costing,
            round_trip=constraints.round_trip,
            anchor=anchor,
        )
    else:
        order, info = _nn_2opt_multi(
            candidates,
            matrix,
            visits,
            budget_s,
            info,
            round_trip=constraints.round_trip,
            anchor=anchor,
        )

    must_ids = set(constraints.must_visit_ids)
    constrained, dropped = _budget_constrain(
        candidates,
        order,
        matrix,
        visits,
        budget_s,
        must_ids=must_ids,
        costing=costing,
        round_trip=constraints.round_trip,
        anchor=anchor,
    )
    if constrained != list(range(n)):
        new_order = [i for i in constrained if i < len(candidates)]
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
    info: dict[str, Any],
    order: list[int],
    candidates: list[Candidate],
    constraints: ResolvedConstraints,
) -> dict[str, Any]:
    """Surfaced as ``missing_must_visit_ids`` in ``info`` so the verifier can mark
    the requirement unmet/infeasible.
    """
    if not constraints.must_visit_ids:
        return info
    final_ids = {candidates[i].id for i in order if 0 <= i < len(candidates)}
    missing = [mid for mid in dict.fromkeys(constraints.must_visit_ids) if mid not in final_ids]
    if missing:
        return {**info, "missing_must_visit_ids": missing}
    return info


def _leg_value(cell: float) -> float:
    """NaN contributes 0; an infinite/sentinel cell contributes UNREACHABLE_S so the
    order stays infeasible.
    """
    if math.isnan(cell):
        return 0.0
    if not math.isfinite(cell):
        return float(constants.UNREACHABLE_S)
    return cell


def _order_total(
    order: list[int],
    matrix: list[list[float]],
    visits: list[int],
    round_trip: bool = False,
    anchor: OriginAnchor | None = None,
) -> float:
    """Total route time (walk + visit), the tourist's own legs included."""
    total = total_seconds(order, matrix, visits)
    if anchor is not None and order:
        total += _leg_value(leg_seconds(matrix, anchor, ORIGIN_INDEX, order[0]))
    total += _closing_leg(order, matrix, round_trip, anchor)
    return total


def _closing_leg(
    order: list[int],
    matrix: list[list[float]],
    round_trip: bool,
    anchor: OriginAnchor | None,
) -> float:
    """The one leg the pairwise chain does not carry: the way back."""
    if not round_trip or len(order) < constants.MIN_LEG_POINTS:
        return 0.0
    if anchor is not None:
        return _leg_value(leg_seconds(matrix, anchor, order[-1], ORIGIN_INDEX))
    return _leg_value(matrix[order[-1]][order[0]])


def _measured_total(
    order: list[int],
    matrix: list[list[float]],
    visits: list[int],
    round_trip: bool,
    anchor: OriginAnchor | None = None,
) -> float:
    """An unreachable/sentinel leg is not summed into the budget; the leg constraint
    handles it separately.
    """
    total = float(visit_cost(order, visits))
    for a, b in route_legs(order, round_trip, anchor):
        cell = leg_seconds(matrix, anchor, a, b)
        if math.isfinite(cell):
            total += cell
    return total


def _leg_rule_legs(
    order: list[int], round_trip: bool, anchor: OriginAnchor | None
) -> list[tuple[int, int]]:
    """The legs the walkability rule applies to."""
    if anchor is not None:
        return list(pairwise(order))
    return route_legs(order, round_trip, None)


def _worst_leg(
    order: list[int],
    matrix: list[list[float]],
    round_trip: bool,
    anchor: OriginAnchor | None = None,
) -> tuple[int, int] | None:
    """The longest real leg of the route (NaN/"could not ask" legs skipped)."""
    worst: tuple[int, int] | None = None
    for a, b in _leg_rule_legs(order, round_trip, anchor):
        cell = matrix[a][b]
        if math.isnan(cell):
            continue
        if worst is None or cell > matrix[worst[0]][worst[1]]:
            worst = (a, b)
    return worst


def _most_saving(
    removable: list[int], route: list[int], total_of: Callable[[list[int]], float]
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
    *,
    must_ids: set[int],
    costing: str = "pedestrian",
    round_trip: bool = False,
    anchor: OriginAnchor | None = None,
) -> tuple[list[int], int]:
    """Greedily trim `order` to fit inside the time budget and max-leg constraint."""
    if not order:
        return order, 0

    route = list(order)
    must_idx_set = {i for i in route if candidates[i].id in must_ids}
    dropped = 0

    max_leg_s = _max_leg_seconds(budget_s, costing)

    def total_of(r: list[int]) -> float:
        return _measured_total(r, matrix, visits, round_trip, anchor)

    while len(route) > 1:
        leg = _worst_leg(route, matrix, round_trip, anchor)
        leg_ok = leg is None or matrix[leg[0]][leg[1]] <= max_leg_s
        budget_ok = budget_s is None or total_of(route) <= budget_s
        if budget_ok and leg_ok:
            break

        removable = [i for i in route if i not in must_idx_set]
        if not removable:
            break

        if not leg_ok and leg is not None:
            endpoints = [x for x in leg if x in removable]
            if endpoints:
                to_remove = _most_saving(endpoints, route, total_of)
                route.remove(to_remove)
                dropped += 1
                continue
            if budget_ok:
                break

        to_remove = _most_saving(removable, route, total_of)
        route.remove(to_remove)
        dropped += 1

    if dropped:
        route = _two_opt(route, matrix)

    return route, dropped


def _max_leg_seconds(budget_s: int | None, costing: str = "pedestrian") -> float:
    """Maximum allowed time for a single leg, in seconds."""
    costing = costing_for(costing)
    if costing == "bicycle":
        ride_s = (constants.BICYCLE_LEG_KM / constants.BICYCLE_SPEED_KMH) * 3600
        if budget_s is None:
            return ride_s
        return min(budget_s * constants.WALK_LEG_BUDGET_SHARE, ride_s)
    if costing != "pedestrian":
        return float(budget_s) if budget_s else constants.DRIVE_LEG_FALLBACK_S
    km_limit_s = (constants.MAX_WALK_LEG_KM / constants.PEDESTRIAN_SPEED_KMH) * 3600
    if budget_s is None:
        return km_limit_s
    return min(budget_s * constants.WALK_LEG_BUDGET_SHARE, km_limit_s)


def _max_leg(
    route: list[int],
    matrix: list[list[float]],
    round_trip: bool = False,
    anchor: OriginAnchor | None = None,
) -> float:
    """Longest single leg of the tour itself, in seconds."""
    best = 0.0
    for a, b in _leg_rule_legs(route, round_trip, anchor):
        cell = matrix[a][b]
        if math.isnan(cell):
            continue
        best = max(best, cell)
    return best


def _brute_open(
    cands: list[Candidate],
    matrix: list[list[float]],
    visits: list[int],
    budget_s: int | None,
    info: dict[str, Any],
    *,
    round_trip: bool = False,
    anchor: OriginAnchor | None = None,
) -> tuple[list[int], dict[str, Any]]:
    """Enumerate (start, end) × all permutations of middle, return cheapest."""
    n = len(cands)
    best_route: list[int] | None = None
    best_cost = float("inf")
    best_total = float("inf")

    for start in range(n):
        for end in range(n):
            if start == end:
                continue
            middle = [i for i in range(n) if i not in (start, end)]
            for perm in permutations(middle):
                order = [start, *perm, end]
                total = _order_total(order, matrix, visits, round_trip, anchor)
                walk = walk_cost(order, matrix)
                if budget_s is not None and total > budget_s:
                    continue
                if total < best_total:
                    best_total = total
                    best_route = order
                    best_cost = walk

    if best_route is None:
        for start in range(n):
            for end in range(n):
                if start == end:
                    continue
                middle = [i for i in range(n) if i not in (start, end)]
                for perm in permutations(middle):
                    order = [start, *perm, end]
                    total = _order_total(order, matrix, visits, round_trip, anchor)
                    if total < best_total:
                        best_total = total
                        best_route = order

    if best_route is None:
        best_route = list(range(n))
        best_total = _order_total(best_route, matrix, visits, round_trip, anchor)

    info.update(
        {
            "algorithm": "brute_open",
            "order": best_route,
            "walk_seconds": best_cost,
            "total_seconds": int(best_total),
        }
    )
    return best_route, info


def _regret_insertion(
    cands: list[Candidate],
    matrix: list[list[float]],
    visits: list[int],
    budget_s: int | None,
    must_idx: list[int],
    *,
    info: dict[str, Any],
    costing: str = "pedestrian",
    round_trip: bool = False,
    anchor: OriginAnchor | None = None,
) -> tuple[list[int], dict[str, Any]]:
    """Sequentially insert the most "regrettable-to-skip" candidate."""
    n = len(cands)
    if must_idx:
        route = list(must_idx)
        remaining = [i for i in range(n) if i not in must_idx]
    else:
        seed = max(range(n), key=lambda i: cands[i].relevance)
        route = [seed]
        remaining = [i for i in range(n) if i != seed]

    iterations = 0
    skipped = 0
    max_leg_s = _max_leg_seconds(budget_s, costing=costing)
    while remaining:
        cur_cost = _order_total(route, matrix, visits, round_trip, anchor)
        if budget_s is not None and cur_cost >= budget_s:
            break

        best_choice: tuple[float, int, int] | None = None

        for c in remaining:
            insertion_costs: list[tuple[float, int]] = []
            for pos in range(len(route) + 1):
                new_route = [*route[:pos], c, *route[pos:]]
                if _max_leg(new_route, matrix, round_trip, anchor) > max_leg_s:
                    continue
                cost = _order_total(new_route, matrix, visits, round_trip, anchor)
                insertion_costs.append((cost, pos))
            if not insertion_costs:
                continue
            insertion_costs.sort()
            regret = (
                insertion_costs[1][0] - insertion_costs[0][0] if len(insertion_costs) > 1 else 0
            )
            if best_choice is None or regret > best_choice[0]:
                best_choice = (regret, c, insertion_costs[0][1])

        if best_choice is None:
            break
        _, chosen, pos = best_choice
        new_route = [*route[:pos], chosen, *route[pos:]]
        if (
            budget_s is not None
            and _order_total(new_route, matrix, visits, round_trip, anchor) > budget_s
        ):
            remaining.remove(chosen)
            skipped += 1
            continue
        route = new_route
        remaining.remove(chosen)
        iterations += 1

    info.update(
        {
            "algorithm": "regret_2",
            "order": route,
            "iterations": iterations,
            "skipped_over_budget": skipped,
            "walk_seconds": walk_cost(route, matrix),
            "total_seconds": int(_order_total(route, matrix, visits, round_trip, anchor)),
        }
    )
    return route, info


def _nn_2opt_multi(
    cands: list[Candidate],
    matrix: list[list[float]],
    visits: list[int],
    budget_s: int | None,
    info: dict[str, Any],
    *,
    round_trip: bool = False,
    anchor: OriginAnchor | None = None,
) -> tuple[list[int], dict[str, Any]]:
    """Nearest-neighbour from K random starts + 2-opt local search."""
    n = len(cands)
    rng = random.Random(42)
    starts = list(range(n))
    rng.shuffle(starts)
    starts = starts[:NN_STARTS] if n >= NN_STARTS else starts
    if anchor is not None:
        nearest = min(range(n), key=lambda i: leg_seconds(matrix, anchor, ORIGIN_INDEX, i))
        starts = [nearest, *[s for s in starts if s != nearest]][:NN_STARTS]

    best_route: list[int] | None = None
    best_total = float("inf")
    best_walk = float("inf")

    for start in starts:
        route = _nn_order(matrix, start, n)
        route = _two_opt(route, matrix)
        total = _order_total(route, matrix, visits, round_trip, anchor)
        if budget_s is not None and total > budget_s:
            continue
        if total < best_total:
            best_total = total
            best_route = route
            best_walk = walk_cost(route, matrix)

    if best_route is None:
        for start in starts:
            route = _nn_order(matrix, start, n)
            route = _two_opt(route, matrix)
            total = _order_total(route, matrix, visits, round_trip, anchor)
            if total < best_total:
                best_total = total
                best_route = route
                best_walk = walk_cost(route, matrix)

    if best_route is None:
        best_route = list(range(n))
        best_total = _order_total(best_route, matrix, visits, round_trip, anchor)
        best_walk = walk_cost(best_route, matrix)

    info.update(
        {
            "algorithm": "nn_2opt_multi",
            "order": best_route,
            "walk_seconds": best_walk,
            "total_seconds": int(best_total),
        }
    )
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
    """A reversal is accepted only when it lowers the order's REAL cost — the sum
    `walk_cost` measures — not merely the two boundary edges.
    """
    if len(order) <= constants.MIN_TOUR_STOPS:
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
                    continue
                old = matrix[a][b] + matrix[c][d]
                new = matrix[a][c] + matrix[b][d]
                if not new + 1e-6 < old:
                    continue
                candidate = order[:i] + order[i : j + 1][::-1] + order[j + 1 :]
                candidate_cost = walk_cost(candidate, matrix)
                if candidate_cost + 1e-6 < current:
                    order = candidate
                    current = candidate_cost
                    improved = True
    return order


def valhalla_order(
    route: list[Candidate],
    info: dict[str, Any],
    *,
    costing: str,
    timeout: float | None = None,
    retries: int | None = None,
    anchor: OriginAnchor | None = None,
) -> tuple[list[Candidate], dict[str, Any]]:
    """Let Valhalla order the walk when nothing pins the sequence."""
    if anchor is not None:
        return route, {**info, "valhalla_order": "skipped_origin_known"}
    if len(route) < constants.MIN_TOUR_STOPS:
        return route, info
    coords = [{"lat": c.lat, "lon": c.lon} for c in route]
    try:
        v_order, _, _ = valhalla_optimized_route(
            coords, costing=costing, timeout=timeout, retries=retries
        )
    except UpstreamUnavailableError:
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


def prune_unroutable(
    route: list[Candidate],
    candidates: list[Candidate],
    cost: CostMatrix,
    must_visit_ids: list[int] | None = None,
) -> tuple[list[Candidate], list[Any]]:
    """Drop stops the matrix cannot connect to their predecessor in this order."""
    route, pruned = prune_unroutable_stops(route, candidates, cost, must_visit_ids=must_visit_ids)
    if pruned:
        log.warning(
            "pruned %d stop(s) Valhalla cannot reach in this order: %s",
            len(pruned),
            ", ".join(c.name for c in pruned),
        )
    if len(route) < constants.MIN_LEG_POINTS:
        raise NoRoutePossibleError(
            "Valhalla не нашла дороги между нашими точками — уточните город или район"
        )
    return route, pruned


def order_after_prune(
    info: dict[str, Any], route: list[Candidate], candidates: list[Candidate]
) -> dict[str, Any]:
    """``info["order"]`` holds indices into the cost matrix, so a shortened route
    needs them recomputed.
    """
    order = info.get("order") or []
    if len(order) == len(route):
        return info
    position = {id(c): i for i, c in enumerate(candidates)}
    return {**info, "order": [position[id(c)] for c in route]}

"""Step 5 — Compute the cost matrix."""

from __future__ import annotations

import itertools
import math
from collections.abc import Collection
from dataclasses import dataclass

from core import constants
from core.errors import NoRoutePossibleError, UpstreamUnavailableError
from routing.models import (
    Candidate,
    CategoryFacts,
    CostMatrix,
    LatLon,
    OriginAnchor,
    ResolvedConstraints,
)
from routing.valhalla.constants import costing_for
from routing.valhalla.matrix import time_matrix
from routing.valhalla.snap import snap_locations

from .geometry import distance_m, distance_pt_m

REASON_UNROUTABLE_LEG = "unroutable_leg"
REASON_MUST_VISIT_UNROUTABLE = "must_visit_unroutable"


@dataclass(frozen=True)
class PrunedStop:
    """Not every entry was *removed*: a mandatory stop is kept and reported with
    ``must_visit_unroutable``.
    """

    candidate: Candidate
    reason: str

    @property
    def id(self) -> int:
        return self.candidate.id

    @property
    def name(self) -> str:
        return self.candidate.name


VISIT_CAP_BUDGET_SHARE = 0.4
MIN_VISIT_MINUTES = 10


DEFAULT_VISIT_MINUTES = 15


def visit_time_minutes(category: str | None, facts: CategoryFacts) -> int:
    """Estimated minutes to look around a place of a given category."""
    return facts.minutes_for(category) or DEFAULT_VISIT_MINUTES


def visit_minutes_of(candidate: Candidate, facts: CategoryFacts) -> int:
    """The stop's own visit estimate, falling back to its category default."""
    if candidate.visit_minutes_db is not None:
        return int(candidate.visit_minutes_db)
    return visit_time_minutes(candidate.category, facts)


def _measure_anchor(
    origin: LatLon | None,
    coords: list[dict[str, float]],
    costing: str,
    deadline: float | None,
) -> OriginAnchor | None:
    """Measure the tourist's own two legs, through the same matrix call as the rest."""
    if origin is None or not coords:
        return None
    point = snap_locations([{"lat": origin.lat, "lon": origin.lon}], costing=costing)
    try:
        from_origin = time_matrix(point, coords, costing=costing, deadline=deadline)
        to_origin = time_matrix(coords, point, costing=costing, deadline=deadline)
    except UpstreamUnavailableError:
        return None
    if len(from_origin) != 1 or len(from_origin[0]) != len(coords):
        return None
    if len(to_origin) != len(coords) or any(len(row) != 1 for row in to_origin):
        return None
    return OriginAnchor(
        from_origin=list(from_origin[0]),
        to_origin=[row[0] for row in to_origin],
    )


def compute_cost_matrix(
    candidates: list[Candidate],
    constraints: ResolvedConstraints,
    costing: str,
    facts: CategoryFacts,
    deadline: float | None = None,
) -> CostMatrix:
    """Valhalla sources_to_targets + visit times, with sanity pre-filter."""
    if not candidates:
        return CostMatrix(walk_seconds=[], visit_minutes=[], indices=[])

    coords = [{"lat": c.lat, "lon": c.lon} for c in candidates]
    coords = snap_locations(coords, costing=costing)
    anchor = _measure_anchor(constraints.origin, coords, costing, deadline)
    matrix = time_matrix(coords, coords, costing=costing, deadline=deadline)

    if not matrix or len(matrix) != len(candidates):
        raise UpstreamUnavailableError(
            f"valhalla time_matrix returned malformed shape: "
            f"expected {len(candidates)}x{len(candidates)}, got {len(matrix)} rows"
        )

    raw_visits = [c.visit_minutes_db or visit_time_minutes(c.category, facts) for c in candidates]

    if constraints.time_budget_minutes:
        cap = max(
            MIN_VISIT_MINUTES,
            int(constraints.time_budget_minutes * VISIT_CAP_BUDGET_SHARE),
        )
        visits = [min(v, cap) for v in raw_visits]
    else:
        visits = raw_visits

    keep_idx = list(range(len(candidates)))

    kept_original_idx = keep_idx
    if len(keep_idx) != len(candidates):
        candidates = [candidates[i] for i in keep_idx]
        visits = [visits[i] for i in keep_idx]
        coords = [{"lat": c.lat, "lon": c.lon} for c in candidates]
        coords = snap_locations(coords, costing=costing)
        matrix = time_matrix(coords, coords, costing=costing)

    return CostMatrix(
        walk_seconds=matrix,
        visit_minutes=visits,
        indices=kept_original_idx,
        anchor=anchor,
    )


def _usable_link(value: float) -> bool:
    """UNKNOWN_S (NaN) counts as usable; only UNREACHABLE_S (or an infinite cell)
    marks a pair as impossible.
    """
    return math.isnan(value) or (math.isfinite(value) and value < constants.UNREACHABLE_S)


def _sub_matrix(cost: CostMatrix, idx: list[int]) -> CostMatrix:
    anchor = cost.anchor
    if anchor is not None:
        anchor = OriginAnchor(
            from_origin=[anchor.from_origin[i] for i in idx],
            to_origin=[anchor.to_origin[i] for i in idx],
        )
    return CostMatrix(
        walk_seconds=[[cost.walk_seconds[i][j] for j in idx] for i in idx],
        visit_minutes=[cost.visit_minutes[i] for i in idx],
        indices=[cost.indices[i] for i in idx],
        anchor=anchor,
    )


def _best_pair(candidates: list[Candidate], cost: CostMatrix) -> tuple[list[Candidate], CostMatrix]:
    """The best two surviving candidates when nothing else is reachable."""
    n = len(candidates)
    best: tuple[tuple[int, float], int, int] | None = None
    for i in range(n):
        for j in range(i + 1, n):
            linked = _usable_link(cost.walk_seconds[i][j]) or _usable_link(cost.walk_seconds[j][i])
            key = (1 if linked else 0, candidates[i].relevance + candidates[j].relevance)
            if best is None or key > best[0]:
                best = (key, i, j)
    assert best is not None
    _, i, j = best
    return [candidates[i], candidates[j]], _sub_matrix(cost, [i, j])


def drop_unreachable(
    candidates: list[Candidate], cost: CostMatrix
) -> tuple[list[Candidate], CostMatrix]:
    """A stop survives only when it can reach a surviving stop AND be reached from
    one; when fewer than two survive, the best pair is returned.
    """
    n = len(candidates)
    if n < constants.MIN_TOUR_STOPS:
        return candidates, cost

    alive = set(range(n))
    changed = True
    while changed:
        changed = False
        for i in sorted(alive):
            can_reach = any(j != i and _usable_link(cost.walk_seconds[i][j]) for j in alive)
            reachable_from = any(j != i and _usable_link(cost.walk_seconds[j][i]) for j in alive)
            if not (can_reach and reachable_from):
                alive.discard(i)
                changed = True

    if len(alive) == n:
        return candidates, cost
    if len(alive) < constants.MIN_LEG_POINTS:
        return _best_pair(candidates, cost)

    idx = sorted(alive)
    return [candidates[i] for i in idx], _sub_matrix(cost, idx)


def prune_unroutable_stops(
    route: list[Candidate],
    candidates: list[Candidate],
    cost: CostMatrix,
    must_visit_ids: Collection[int] | None = None,
) -> tuple[list[Candidate], list[PrunedStop]]:
    """Must-visit stops are never dropped: they stay and are reported with
    ``must_visit_unroutable``. Returns ``(kept_route, report)``.
    """
    if len(route) < constants.MIN_TOUR_STOPS:
        return route, []
    index = {c.id: i for i, c in enumerate(candidates)}
    must = set(must_visit_ids or ())
    keep = list(route)
    report: list[PrunedStop] = []

    def leg_bad(a: Candidate, b: Candidate) -> bool:
        i, j = index.get(a.id), index.get(b.id)
        if i is None or j is None:
            return False
        cell = cost.walk_seconds[i][j]
        if math.isnan(cell):
            return False
        return not math.isfinite(cell) or cell >= constants.UNREACHABLE_S

    changed = True
    while changed and len(keep) >= constants.MIN_TOUR_STOPS:
        changed = False
        for i in range(len(keep) - 1):
            if not leg_bad(keep[i], keep[i + 1]):
                continue
            nxt = keep[i + 1]
            if nxt.id in must:
                if not any(p.candidate.id == nxt.id for p in report):
                    report.append(PrunedStop(nxt, REASON_MUST_VISIT_UNROUTABLE))
                continue
            report.append(PrunedStop(nxt, REASON_UNROUTABLE_LEG))
            keep.pop(i + 1)
            changed = True
            break
    return keep, report


def walk_cost(order: list[int], matrix: list[list[float]]) -> float:
    """An infinite cell saturates at UNREACHABLE_S; a NaN cell (UNKNOWN_S) is
    skipped, not saturated.
    """
    if len(order) < constants.MIN_LEG_POINTS:
        return 0.0
    total = 0.0
    for a, b in itertools.pairwise(order):
        cell = matrix[a][b]
        if math.isnan(cell):
            continue
        if not math.isfinite(cell):
            return float(constants.UNREACHABLE_S)
        total += cell
        if total >= constants.UNREACHABLE_S:
            return float(constants.UNREACHABLE_S)
    return total


def visit_cost(order: list[int], visit_minutes: list[int]) -> int:
    """Sum visit_minutes along an ordered list of candidate indices."""
    return int(sum(visit_minutes[i] for i in order)) * 60


def total_seconds(order: list[int], matrix: list[list[float]], visit_minutes: list[int]) -> int:
    return int(walk_cost(order, matrix)) + visit_cost(order, visit_minutes)


ORIGIN_INDEX = -1


def leg_seconds(matrix: list[list[float]], anchor: OriginAnchor | None, a: int, b: int) -> float:
    """One leg's time in seconds; the origin has its own columns, not a row."""
    if a == ORIGIN_INDEX:
        return anchor.from_origin[b] if anchor is not None else constants.UNKNOWN_S
    if b == ORIGIN_INDEX:
        return anchor.to_origin[a] if anchor is not None else constants.UNKNOWN_S
    return matrix[a][b]


def route_legs(
    order: list[int], round_trip: bool, anchor: OriginAnchor | None = None
) -> list[tuple[int, int]]:
    """Every leg of a route, the tourist's own legs included when they are known."""
    if not order:
        return []
    legs = list(itertools.pairwise(order))
    if anchor is not None:
        legs.insert(0, (ORIGIN_INDEX, order[0]))
        if round_trip:
            legs.append((order[-1], ORIGIN_INDEX))
    elif round_trip and len(order) >= constants.MIN_LEG_POINTS:
        legs.append((order[-1], order[0]))
    return legs


def _fallback_speed_ms(costing: str) -> float:
    """The straight-line pace to assume when the engine cannot answer."""
    kmh = {
        "pedestrian": constants.PEDESTRIAN_SPEED_KMH,
        "bicycle": constants.BICYCLE_SPEED_KMH,
    }.get(costing_for(costing), constants.AUTO_SPEED_KMH)
    return kmh / 3.6


def _synthetic_cost(
    candidates: list[Candidate],
    costing: str,
    facts: CategoryFacts,
    origin: LatLon | None = None,
) -> CostMatrix:
    """A straight-line cost matrix for when Valhalla cannot answer."""
    speed_ms = _fallback_speed_ms(costing)
    n = len(candidates)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                matrix[i][j] = distance_m(candidates[i], candidates[j]) / speed_ms
    anchor = None
    if origin is not None:
        anchor = OriginAnchor(
            from_origin=[
                distance_pt_m(origin.lat, origin.lon, c.lat, c.lon) / speed_ms for c in candidates
            ],
            to_origin=[
                distance_pt_m(c.lat, c.lon, origin.lat, origin.lon) / speed_ms for c in candidates
            ],
        )
    return CostMatrix(
        walk_seconds=matrix,
        visit_minutes=[visit_minutes_of(c, facts) for c in candidates],
        indices=list(range(n)),
        anchor=anchor,
    )


def refinement_cost(
    route: list[Candidate],
    constraints: ResolvedConstraints,
    costing: str,
    facts: CategoryFacts,
) -> tuple[list[Candidate], CostMatrix]:
    """Never raises and never drops a stop the user kept: falls back to straight-line
    legs if the pre-filter would remove one.
    """
    base_ids = {c.id for c in route}
    try:
        cost = compute_cost_matrix(route, constraints, costing, facts)
    except (UpstreamUnavailableError, NoRoutePossibleError):
        return route, _synthetic_cost(route, costing, facts, constraints.origin)

    if cost.indices != list(range(len(route))):
        aligned = [route[i] for i in cost.indices]
        if not base_ids <= {c.id for c in aligned}:
            return route, _synthetic_cost(route, costing, facts, constraints.origin)
        route = aligned
        cost = CostMatrix(
            walk_seconds=cost.walk_seconds,
            visit_minutes=cost.visit_minutes,
            indices=list(range(len(route))),
            anchor=cost.anchor,
        )
    return route, cost


def build_cost(
    candidates: list[Candidate],
    constraints: ResolvedConstraints,
    costing: str,
    facts: CategoryFacts,
) -> tuple[list[Candidate], CostMatrix]:
    """Cost matrix, aligned to the candidates, minus what Valhalla cannot reach."""
    cost = compute_cost_matrix(candidates, constraints, costing, facts)
    if cost.indices != list(range(len(candidates))):
        candidates = [candidates[i] for i in cost.indices]
    candidates, cost = drop_unreachable(candidates, cost)
    if len(candidates) < constants.MIN_LEG_POINTS:
        raise NoRoutePossibleError(
            "Valhalla не нашла дороги между нашими точками — уточните город или район"
        )
    return candidates, cost

"""Step 5 — Compute the cost matrix."""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from core import constants
from core.errors import NoRoutePossible, UpstreamUnavailable
from planner.models import Candidate, CostMatrix, ResolvedConstraints
from planner.valhalla_client import snap_locations, time_matrix
from reference import taxonomy

from .geo import _distance_m

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


def visit_time_minutes(category: str | None) -> int:
    """Estimated minutes to look around a place of a given category."""
    if not category:
        return DEFAULT_VISIT_MINUTES
    try:
        code = (
            category
            if category in taxonomy.all_codes()
            else taxonomy.resolve_code(category)
        )
        if code is None:
            return DEFAULT_VISIT_MINUTES
        return taxonomy.visit_minutes(code)
    except KeyError:  # pragma: no cover — resolve_code only returns known codes
        return DEFAULT_VISIT_MINUTES


def compute_cost_matrix(
    candidates: list[Candidate],
    constraints: ResolvedConstraints,
    costing: str = "pedestrian",
    deadline: float | None = None,
) -> CostMatrix:
    """Valhalla sources_to_targets + visit times, with sanity pre-filter."""
    if not candidates:
        return CostMatrix(walk_seconds=[], visit_minutes=[], indices=[])

    coords = [{"lat": c.lat, "lon": c.lon} for c in candidates]
    coords = snap_locations(coords, costing=costing)
    try:
        matrix = time_matrix(coords, coords, costing=costing, deadline=deadline)
    except UpstreamUnavailable:
        raise

    if not matrix or len(matrix) != len(candidates):
        raise UpstreamUnavailable(
            f"valhalla time_matrix returned malformed shape: "
            f"expected {len(candidates)}x{len(candidates)}, got {len(matrix)} rows"
        )

    raw_visits = [c.visit_minutes_db or visit_time_minutes(c.category) for c in candidates]

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
    )


def _usable_link(value: float) -> bool:
    """UNKNOWN_S (NaN) counts as usable; only UNREACHABLE_S (or an infinite cell)
    marks a pair as impossible.
    """
    return math.isnan(value) or (math.isfinite(value) and value < constants.UNREACHABLE_S)


def _sub_matrix(cost: CostMatrix, idx: list[int]) -> CostMatrix:
    return CostMatrix(
        walk_seconds=[[cost.walk_seconds[i][j] for j in idx] for i in idx],
        visit_minutes=[cost.visit_minutes[i] for i in idx],
        indices=[cost.indices[i] for i in idx],
    )


def _best_pair(
    candidates: list[Candidate], cost: CostMatrix
) -> tuple[list[Candidate], CostMatrix]:
    """The best two surviving candidates when nothing else is reachable."""
    n = len(candidates)
    best: tuple[tuple[int, float], int, int] | None = None
    for i in range(n):
        for j in range(i + 1, n):
            linked = _usable_link(cost.walk_seconds[i][j]) or _usable_link(
                cost.walk_seconds[j][i]
            )
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
    if n < 3:
        return candidates, cost

    alive = set(range(n))
    changed = True
    while changed:
        changed = False
        for i in sorted(alive):
            can_reach = any(
                j != i and _usable_link(cost.walk_seconds[i][j]) for j in alive
            )
            reachable_from = any(
                j != i and _usable_link(cost.walk_seconds[j][i]) for j in alive
            )
            if not (can_reach and reachable_from):
                alive.discard(i)
                changed = True

    if len(alive) == n:
        return candidates, cost
    if len(alive) < 2:
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
    if len(route) < 3:
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
    while changed and len(keep) >= 3:
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
    if len(order) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(order, order[1:]):
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


def total_seconds(
    order: list[int], matrix: list[list[float]], visit_minutes: list[int]
) -> int:
    return int(walk_cost(order, matrix)) + visit_cost(order, visit_minutes)


def _synthetic_cost(
    candidates: list[Candidate], costing: str = "pedestrian"
) -> CostMatrix:
    """A straight-line cost matrix for when Valhalla cannot answer."""
    from .refine import visit_minutes_of

    speed_ms = 1.3 if costing == "pedestrian" else 8.0
    n = len(candidates)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                matrix[i][j] = _distance_m(candidates[i], candidates[j]) / speed_ms
    return CostMatrix(
        walk_seconds=matrix,
        visit_minutes=[visit_minutes_of(c) for c in candidates],
        indices=list(range(n)),
    )


def _refinement_cost(
    route: list[Candidate], constraints: ResolvedConstraints, costing: str
) -> tuple[list[Candidate], CostMatrix]:
    """Never raises and never drops a stop the user kept: falls back to straight-line
    legs if the pre-filter would remove one.
    """
    base_ids = {c.id for c in route}
    try:
        cost = compute_cost_matrix(route, constraints, costing=costing)
    except (UpstreamUnavailable, NoRoutePossible):
        return route, _synthetic_cost(route, costing)

    if cost.indices != list(range(len(route))):
        aligned = [route[i] for i in cost.indices]
        if not base_ids <= {c.id for c in aligned}:
            return route, _synthetic_cost(route, costing)
        route = aligned
        cost = CostMatrix(
            walk_seconds=cost.walk_seconds,
            visit_minutes=cost.visit_minutes,
            indices=list(range(len(route))),
        )
    return route, cost


def _build_cost(
    candidates: list[Candidate], constraints: ResolvedConstraints, costing: str
) -> tuple[list[Candidate], CostMatrix]:
    """Cost matrix, aligned to the candidates, minus what Valhalla cannot reach."""
    cost = compute_cost_matrix(candidates, constraints, costing=costing)
    if cost.indices != list(range(len(candidates))):
        candidates = [candidates[i] for i in cost.indices]
    candidates, cost = drop_unreachable(candidates, cost)
    if len(candidates) < 2:
        raise NoRoutePossible(
            "Valhalla не нашла дороги между нашими точками — "
            "уточните город или район"
        )
    return candidates, cost


def _is_service_code(code: str) -> bool:
    """True when a taxonomy code is a service (a café, a toilet, a hotel)."""
    try:
        return taxonomy.role(code) == "service"
    except Exception:
        return False


def _is_sight_stop(candidate: Any) -> bool:
    """True when a candidate is a destination, not a service the user asked for.

    Unknown category codes count as sights.
    """
    try:
        return taxonomy.role(candidate.category) != "service"
    except Exception:
        return True

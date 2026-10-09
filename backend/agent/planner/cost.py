"""Step 5 — Compute the cost matrix.

Two components:
  * walk_seconds: NxN Valhalla time_matrix (seconds)
  * visit_minutes: per-candidate estimated visit time

Pre-filter: candidates whose visit_time exceeds a sane share of the budget
are dropped (they can't fit in a reasonable route alongside anything else).
The threshold is 40% — empirical, tuned so a 40-min "замок" can still fit
into a 2-hour route together with one or two shorter stops.

Also re-exports `visit_time_minutes` (moved from agent/routing.py during
the planner refactor) — kept here because every per-place visit cost
comes from this function and pipeline.py needs it for the Place response.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass

from contracts.planner import Candidate, CostMatrix, ResolvedConstraints
from core.errors import UpstreamUnavailable
from domain import constants, taxonomy
from infra.valhalla_client import snap_locations, time_matrix

# Machine reason codes for prune_unroutable_stops (localized by the API layer).
REASON_UNROUTABLE_LEG = "unroutable_leg"
REASON_MUST_VISIT_UNROUTABLE = "must_visit_unroutable"


@dataclass(frozen=True)
class PrunedStop:
    """A stop flagged by prune_unroutable_stops, with a machine reason.

    Not every entry was *removed*: a mandatory stop whose incoming leg cannot
    be routed is kept on the route and reported with
    ``reason == "must_visit_unroutable"``. ``.id`` / ``.name`` proxy the
    candidate so existing logging (``", ".join(c.name for c in report)``) and
    tests keep working.
    """

    candidate: Candidate
    reason: str

    @property
    def id(self) -> int:
        return self.candidate.id

    @property
    def name(self) -> str:
        return self.candidate.name


# A single stop may claim at most this share of the time budget; see the cap in
# compute_cost_matrix().
VISIT_CAP_BUDGET_SHARE = 0.4
# …but never below this many minutes, so short walks don't cap everything to 5.
MIN_VISIT_MINUTES = 10


# Visit-time estimation (moved from agent/routing.py)

#: Visit time for a category the taxonomy does not know — a free-text or legacy
#: DB value. Canonical codes all resolve through data/taxonomy.csv; this is the
#: honest last resort for the rest.
DEFAULT_VISIT_MINUTES = 15


def visit_time_minutes(category: str | None) -> int:
    """Estimated minutes to look around a place of a given category.

    The number comes from the canonical taxonomy (``data/taxonomy.csv``) through
    ``taxonomy.visit_minutes`` — ONE source, no second table to drift. Compound
    or free-text DB categories ("католический костёл", "кафе-кондитерская") are
    resolved to their code first; anything the taxonomy cannot place gets
    ``DEFAULT_VISIT_MINUTES``.
    """
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


# Cost matrix computation

def compute_cost_matrix(
    candidates: list[Candidate],
    constraints: ResolvedConstraints,
    costing: str = "pedestrian",
) -> CostMatrix:
    """Valhalla sources_to_targets + visit times, with sanity pre-filter."""
    if not candidates:
        return CostMatrix(walk_seconds=[], visit_minutes=[], indices=[])

    # Valhalla matrix
    coords = [{"lat": c.lat, "lon": c.lon} for c in candidates]
    # Snap every POI onto the routing graph first (one cached /locate per point):
    # the walk times and the drawn route then both come from real network
    # geometry instead of from a point Valhalla cannot place.
    coords = snap_locations(coords, costing=costing)
    try:
        matrix = time_matrix(coords, coords, costing=costing)
    except UpstreamUnavailable:
        raise

    if not matrix or len(matrix) != len(candidates):
        raise UpstreamUnavailable(
            f"valhalla time_matrix returned malformed shape: "
            f"expected {len(candidates)}x{len(candidates)}, got {len(matrix)} rows"
        )

    # Visit times (curated per-place time from the region dataset, category default)
    raw_visits = [c.visit_minutes_db or visit_time_minutes(c.category) for c in candidates]

    # Cap a single stop's visit time at VISIT_CAP_BUDGET_SHARE of the budget.
    # The curated dataset carries generous per-place times (Mir Castle = 120
    # min), which ate a whole 150-min budget and left the optimizer a 1-stop
    # route.  Capping is honest — the user spent 150 min, the castle gets its
    # share — whereas dropping the place (the old pre-filter) removed the very
    # castle a "замки" query asked for.
    if constraints.time_budget_minutes:
        cap = max(
            MIN_VISIT_MINUTES,
            int(constraints.time_budget_minutes * VISIT_CAP_BUDGET_SHARE),
        )
        visits = [min(v, cap) for v in raw_visits]
    else:
        visits = raw_visits

    # Pre-filter: disabled — Defect-3 fix
    # The pre-filter (original MAX_VISIT_BUDGET_SHARE=0.4) was dropping castle-category
    # places when their estimated visit time (40-60 min) exceeded 40% of the budget.
    # E.g. 40-min castle > 48 min (40% of 120 min) → dropped, even though castles
    # are the most relevant candidates for a "замки" query.
    #
    # The Valhalla matrix handles up to 12 candidates efficiently (2 chunks of 4x4 for 8
    # candidates).  The _budget_constrain step downstream removes stops that don't fit
    # the budget, so the pre-filter is redundant and harmful.
    keep_idx = list(range(len(candidates)))

    # Original positions of the kept candidates, so the caller can align its
    # candidate list with the matrix (pre-filter may drop some rows).
    kept_original_idx = keep_idx
    if len(keep_idx) != len(candidates):
        candidates = [candidates[i] for i in keep_idx]
        visits = [visits[i] for i in keep_idx]
        coords = [{"lat": c.lat, "lon": c.lon} for c in candidates]
        # Snap every POI onto the routing graph first (one /locate per point, cached):
        # the walk times and the drawn route then both come from real network
        # geometry instead of from a point Valhalla cannot place.
        coords = snap_locations(coords, costing=costing)
        matrix = time_matrix(coords, coords, costing=costing)

    return CostMatrix(
        walk_seconds=matrix,
        visit_minutes=visits,
        indices=kept_original_idx,
    )


def drop_unreachable(
    candidates: list[Candidate], cost: CostMatrix
) -> tuple[list[Candidate], CostMatrix]:
    """Remove candidates Valhalla cannot connect to anything else.

    A POI with no reachable partner (e.g. the Grodno-fortress point at
    53.597305,23.800828 — no pedestrian edges anywhere near it, so every pair
    involving it comes back as UNREACHABLE_S) would otherwise make every order
    look impossible and push the optimizer into 422. Dropping it here costs no
    extra Valhalla calls: the matrix rows/columns are already known.
    """
    n = len(candidates)
    if n < 3:
        return candidates, cost

    reachable = []
    for i in range(n):
        ok = any(
            j != i
            and math.isfinite(cost.walk_seconds[i][j])
            and cost.walk_seconds[i][j] < constants.UNREACHABLE_S
            for j in range(n)
        )
        if ok:
            reachable.append(i)

    if len(reachable) == n:
        return candidates, cost
    if len(reachable) < 2:
        return candidates, cost  # nothing to gain, let the caller report it

    return (
        [candidates[i] for i in reachable],
        CostMatrix(
            walk_seconds=[[cost.walk_seconds[i][j] for j in reachable] for i in reachable],
            visit_minutes=[cost.visit_minutes[i] for i in reachable],
            indices=[cost.indices[i] for i in reachable],
        ),
    )


def prune_unroutable_stops(
    route: list[Candidate],
    candidates: list[Candidate],
    cost: CostMatrix,
    must_visit_ids: Collection[int] | None = None,
) -> tuple[list[Candidate], list[PrunedStop]]:
    """Drop stops whose hop from the previous stop Valhalla cannot route at all.

    The matrix already carries Valhalla's own verdict for every pair: an
    unroutable pair comes back as UNREACHABLE_S ("No path could be found for
    input"). A rural chapel can sit on a road island that is connected locally
    but not to the rest of the network for the chosen costing — reachable from
    its neighbour, unreachable from everything else. Ordering such a stop into
    the tour makes /route answer 400 and the UI draw points with no line.

    MUST-VISIT STOPS ARE NEVER DROPPED. When the leg into a mandatory stop is
    unroutable the stop stays on the route and is *reported* with the machine
    reason ``must_visit_unroutable``; the verifier then marks the requirement
    unmet/infeasible. Silently removing something the tourist demanded is
    exactly the failure this guards against.

    Returns ``(kept_route, report)`` where each entry of ``report`` is a
    ``PrunedStop`` carrying the candidate and a machine ``reason``
    (``unroutable_leg`` for a dropped optional stop, ``must_visit_unroutable``
    for a kept mandatory one) — no extra Valhalla calls.

    ``must_visit_ids`` is optional only so existing callers keep working;
    integration passes ``constraints.must_visit_ids``.
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
        return not math.isfinite(cell) or cell >= constants.UNREACHABLE_S

    changed = True
    while changed and len(keep) >= 3:
        changed = False
        for i in range(len(keep) - 1):
            if not leg_bad(keep[i], keep[i + 1]):
                continue
            nxt = keep[i + 1]
            if nxt.id in must:
                # Mandatory: never silently removed — keep it and say why.
                if not any(p.candidate.id == nxt.id for p in report):
                    report.append(PrunedStop(nxt, REASON_MUST_VISIT_UNROUTABLE))
                continue
            # The hop into stop i+1 is impossible → that stop cannot be visited
            # in this order.
            report.append(PrunedStop(nxt, REASON_UNROUTABLE_LEG))
            keep.pop(i + 1)
            changed = True
            break
    return keep, report


def walk_cost(order: list[int], matrix: list[list[float]]) -> float:
    """Sum walk_seconds along an ordered list of candidate indices.

    A non-finite cell (a pair Valhalla cannot connect) makes the whole order
    unusable, so it saturates at UNREACHABLE_S instead of propagating inf:
    every caller compares this against a budget or a max leg, where the sentinel
    behaves exactly like "unreachable", but int(inf) would raise OverflowError.
    """
    if len(order) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(order, order[1:]):
        cell = matrix[a][b]
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

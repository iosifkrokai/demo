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

from ..errors import UpstreamUnavailable
from ..models import Candidate, CostMatrix, ResolvedConstraints
from ..valhalla_client import time_matrix

MAX_VISIT_BUDGET_SHARE = 0.4
# A single stop may claim at most this share of the time budget; see the cap in
# compute_cost_matrix().
VISIT_CAP_BUDGET_SHARE = 0.4
# …but never below this many minutes, so short walks don't cap everything to 5.
MIN_VISIT_MINUTES = 10


# ─────────────────────────────────────────────────────────────────────────────
# Visit-time estimation (moved from agent/routing.py)
# ─────────────────────────────────────────────────────────────────────────────

def visit_time_minutes(category: str | None) -> int:
    """Estimated minutes to look around a place of a given category.

    Aligned with the curated taxonomy in data/places_curated.csv. Compound/
    verbose category strings fall back to substring match.
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


# ─────────────────────────────────────────────────────────────────────────────
# Cost matrix computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_cost_matrix(
    candidates: list[Candidate],
    constraints: ResolvedConstraints,
    costing: str = "pedestrian",
) -> CostMatrix:
    """Valhalla sources_to_targets + visit times, with sanity pre-filter."""
    if not candidates:
        return CostMatrix(walk_seconds=[], visit_minutes=[], indices=[])

    # ── Valhalla matrix ──
    coords = [{"lat": c.lat, "lon": c.lon} for c in candidates]
    try:
        matrix = time_matrix(coords, coords, costing=costing)
    except UpstreamUnavailable:
        raise

    if not matrix or len(matrix) != len(candidates):
        raise UpstreamUnavailable(
            f"valhalla time_matrix returned malformed shape: "
            f"expected {len(candidates)}x{len(candidates)}, got {len(matrix)} rows"
        )

    # ── Visit times (curated per-place time from the region dataset, category default) ──
    raw_visits = [c.visit_minutes_db or visit_time_minutes(c.category) for c in candidates]

    # Cap a single stop's visit time at VISIT_CAP_BUDGET_SHARE of the budget.
    # The curated dataset carries generous per-place times (Мирский замок = 120
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

    # ── Pre-filter: disabled — Defect-3 fix ───────────────────────────────────
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
        matrix = time_matrix(coords, coords, costing=costing)

    return CostMatrix(
        walk_seconds=matrix,
        visit_minutes=visits,
        indices=kept_original_idx,
    )


def walk_cost(order: list[int], matrix: list[list[float]]) -> float:
    """Sum walk_seconds along an ordered list of candidate indices."""
    if len(order) < 2:
        return 0.0
    return float(sum(matrix[a][b] for a, b in zip(order, order[1:])))


def visit_cost(order: list[int], visit_minutes: list[int]) -> int:
    """Sum visit_minutes along an ordered list of candidate indices."""
    return int(sum(visit_minutes[i] for i in order)) * 60


def total_seconds(
    order: list[int], matrix: list[list[float]], visit_minutes: list[int]
) -> int:
    return int(walk_cost(order, matrix)) + visit_cost(order, visit_minutes)

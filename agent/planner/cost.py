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
) -> CostMatrix:
    """Valhalla sources_to_targets + visit times, with sanity pre-filter."""
    if not candidates:
        return CostMatrix(walk_seconds=[], visit_minutes=[], indices=[])

    # ── Valhalla matrix ──
    coords = [{"lat": c.lat, "lon": c.lon} for c in candidates]
    try:
        matrix = time_matrix(coords, coords)
    except UpstreamUnavailable:
        raise

    if not matrix or len(matrix) != len(candidates):
        raise UpstreamUnavailable(
            f"valhalla time_matrix returned malformed shape: "
            f"expected {len(candidates)}x{len(candidates)}, got {len(matrix)} rows"
        )

    # ── Visit times ──
    visits = [visit_time_minutes(c.category) for c in candidates]

    # ── Pre-filter: drop places whose visit_time > 40% of budget (unless they're must-visit) ──
    must_ids = set(constraints.must_visit_ids)
    budget_min = constraints.time_budget_minutes
    keep_idx: list[int] = []
    for i, (c, v) in enumerate(zip(candidates, visits)):
        if c.id in must_ids:
            keep_idx.append(i)
            continue
        if budget_min > 0 and v > budget_min * MAX_VISIT_BUDGET_SHARE:
            continue
        keep_idx.append(i)

    # If pre-filter dropped too much, fall back to keeping the shortest ones.
    if len(keep_idx) < 3 and len(candidates) >= 3:
        sorted_by_visit = sorted(
            range(len(candidates)),
            key=lambda i: (visits[i], -candidates[i].relevance),
        )
        keep_idx = sorted_by_visit[:max(3, len(keep_idx))]

    if len(keep_idx) != len(candidates):
        candidates = [candidates[i] for i in keep_idx]
        visits = [visits[i] for i in keep_idx]
        coords = [{"lat": c.lat, "lon": c.lon} for c in candidates]
        matrix = time_matrix(coords, coords)

    return CostMatrix(
        walk_seconds=matrix,
        visit_minutes=visits,
        indices=list(range(len(candidates))),
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

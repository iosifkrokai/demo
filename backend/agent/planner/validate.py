"""Step 7 — Validation.

After optimize() produces an order, validate() builds the budget summary,
computes diagnostic metrics for the trace, and — when the request's
``TripRequirements`` are supplied — runs the independent verifier against the
built route and records the satisfied/unmet/uncertain breakdown:

  * total_seconds (walk + visit)
  * fits_budget (boolean) — budget checked against the ACTUAL time (path +
    visits), never against the optimizer's claim
  * stops_dropped (if auto-relax triggered)
  * missing_must_visit_ids (mandatory stops the optimizer could not place)
  * diversity (unique categories / n)
  * max_leg_seconds (longest single walking segment)
  * requirement_summary / requirements (when verification ran)

The optimizer's `info` dict carries the original indices into the cost
matrix (`info["order"]`), which we use directly to compute walk/visit
times. The `route` (Candidate objects) is just for display.
"""

from __future__ import annotations

import math
from typing import Any

from .. import constants
from ..models import Candidate, CostMatrix, ResolvedConstraints, ValidatedPlan
from ..requirements import TripRequirements
from .verify import verify, verify_summary


def _leg_s(value: float) -> float:
    """Cell value with non-finite cells saturated to UNREACHABLE_S.

    int(inf) raises OverflowError, and these numbers end up in int() calls and
    in the JSON trace, so saturate instead of propagating a non-finite value.
    """
    return float(constants.UNREACHABLE_S) if not math.isfinite(value) else value


def _leg_sum(matrix: list[list[float]], order: list[int]) -> float:
    if len(order) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(order, order[1:]):
        total += _leg_s(matrix[a][b])
        if total >= constants.UNREACHABLE_S:
            return float(constants.UNREACHABLE_S)
    return total


def _record_optimizer_report(trace: dict, info: dict, prune_report: Any) -> None:
    """Put the optimizer's / pruner's honesty signals into the trace.

    ``missing_must_visit_ids`` comes from optimize() when a mandatory stop could
    not be placed; ``unroutable_stops`` comes from prune_unroutable_stops when a
    leg cannot be routed (a mandatory stop is kept and reported, not removed).
    The verifier reads both back out of the trace.
    """
    if info.get("missing_must_visit_ids"):
        trace["missing_must_visit_ids"] = list(info["missing_must_visit_ids"])
    if prune_report:
        trace["unroutable_stops"] = [
            {
                "id": getattr(p, "id", None),
                "name": getattr(p, "name", None),
                "reason": getattr(p, "reason", None),
            }
            for p in prune_report
        ]


def _run_verification(
    requirements: TripRequirements | None,
    route: list[Candidate],
    trace: dict,
    geometry: Any,
) -> None:
    """Verify the built route and attach the breakdown to the trace."""
    if requirements is None:
        return
    verify(requirements, {"route": route, "trace": trace}, geometry)
    trace["requirements"] = requirements.public_requirements()
    trace["requirement_summary"] = verify_summary(requirements)
    trace["status"] = trace["requirement_summary"]["status"]


def validate(
    route: list[Candidate],
    cost: CostMatrix,
    constraints: ResolvedConstraints,
    info: dict,
    *,
    requirements: TripRequirements | None = None,
    geometry: Any = None,
    prune_report: Any = None,
) -> ValidatedPlan:
    """Budget summary + diagnostics, plus requirement verification when asked.

    ``requirements`` / ``geometry`` / ``prune_report`` are optional so existing
    callers keep working; integration passes the request's ``TripRequirements``
    and the final Valhalla shape so the plan carries a satisfied/unmet/uncertain
    breakdown instead of only a time budget.
    """
    n = len(route)
    if n == 0:
        trace: dict = {"error": "empty_route"}
        _record_optimizer_report(trace, info, prune_report)
        _run_verification(requirements, route, trace, geometry)
        return ValidatedPlan(
            route=[],
            walk_seconds=0.0,
            visit_seconds=0,
            total_seconds=0,
            fits_budget=False,
            stops_dropped=0,
            trace=trace,
        )

    # info["order"] is the list of indices into the original cost matrix
    # (== indices of the candidates passed to optimize).
    order = list(info.get("order") or list(range(n)))
    if len(order) != n:
        # Defensive fallback: the optimizer may have shrunk the route
        # (e.g. budget forced a drop). Use what we have.
        order = list(range(min(len(order), n)))

    matrix = cost.walk_seconds
    visits = cost.visit_minutes

    walk = _leg_sum(matrix, order)
    if constraints.round_trip and len(order) >= 2:
        # «круговой маршрут»: the walk home is real walking time and must count
        # against the budget, or a closed tour would look cheaper than it is.
        walk += _leg_s(matrix[order[-1]][order[0]])
    visits_s = sum(visits[i] for i in order) * 60
    total = int(walk) + int(visits_s)

    # No stated limit → nothing to fit into: the route is whatever was planned.
    budget_s = (constraints.time_budget_minutes or 0) * 60
    fits = total <= budget_s if budget_s > 0 else True

    # Diversity: unique categories / n.
    cats = {c.category for c in route if c.category}
    diversity = len(cats) / n if n else 0.0

    # Longest single walking leg.
    max_leg = max(
        (_leg_s(matrix[a][b]) for a, b in zip(order, order[1:])), default=0.0
    )

    trace = {
        "algorithm": info.get("algorithm"),
        "iterations": info.get("iterations"),
        "categories": [c.category for c in route],
        "category_names": [c.name for c in route if c.category],
        "diversity": round(diversity, 3),
        "max_leg_seconds": int(max_leg),
        "walk_seconds": int(walk),
        "visit_seconds": int(visits_s),
        "total_seconds": total,
        "budget_seconds": budget_s,
        "fits_budget": fits,
        "budget_exceeded": not fits,
    }
    _record_optimizer_report(trace, info, prune_report)
    _run_verification(requirements, route, trace, geometry)

    stops_dropped = int(info.get("stops_dropped", 0))

    return ValidatedPlan(
        route=route,
        walk_seconds=float(walk),
        visit_seconds=int(visits_s),
        total_seconds=total,
        fits_budget=fits,
        stops_dropped=stops_dropped,
        trace=trace,
    )

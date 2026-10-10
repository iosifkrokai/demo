"""Step 7 — Validation.

Builds the budget summary against the ACTUAL time, plus trace diagnostics.
"""

from __future__ import annotations

import math
from typing import Any

from agent.models import TripRequirements
from core import constants
from planner.models import Candidate, CostMatrix, ResolvedConstraints, ValidatedPlan

from .verify import verify, verify_summary


def _leg_s(value: float) -> float:
    """Cell value with non-finite cells saturated to UNREACHABLE_S.

    UNKNOWN_S (NaN) means "we could not ask", so it counts as 0, not unreachable.
    """
    if math.isnan(value):
        return 0.0
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

    ``missing_must_visit_ids`` from optimize(); ``unroutable_stops`` from pruning.
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

    ``requirements`` / ``geometry`` / ``prune_report`` are optional.
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

    order = list(info.get("order") or list(range(n)))
    if len(order) != n:
        order = list(range(min(len(order), n)))

    matrix = cost.walk_seconds
    visits = cost.visit_minutes

    legs = list(zip(order, order[1:]))
    if constraints.round_trip and len(order) >= 2:
        legs.append((order[-1], order[0]))

    walk = _leg_sum(matrix, order)
    if constraints.round_trip and len(order) >= 2:
        walk += _leg_s(matrix[order[-1]][order[0]])
    visits_s = sum(visits[i] for i in order) * 60
    total = int(walk) + int(visits_s)

    budget_s = (constraints.time_budget_minutes or 0) * 60
    fits = total <= budget_s if budget_s > 0 else True

    cats = {c.category for c in route if c.category}
    diversity = len(cats) / n if n else 0.0

    max_leg = max((_leg_s(matrix[a][b]) for a, b in legs), default=0.0)

    unknown_legs = sum(1 for a, b in legs if math.isnan(matrix[a][b]))

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
        "walk_times_unknown": unknown_legs,
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

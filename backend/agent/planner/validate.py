"""Step 7 — Validation.

After optimize() produces an order, validate() builds the budget summary
and computes diagnostic metrics for the trace:

  * total_seconds (walk + visit)
  * fits_budget (boolean)
  * stops_dropped (if auto-relax triggered)
  * diversity (unique categories / n)
  * max_leg_seconds (longest single walking segment)

The optimizer's `info` dict carries the original indices into the cost
matrix (`info["order"]`), which we use directly to compute walk/visit
times. The `route` (Candidate objects) is just for display.
"""

from __future__ import annotations

from ..models import Candidate, CostMatrix, ResolvedConstraints, ValidatedPlan


def validate(
    route: list[Candidate],
    cost: CostMatrix,
    constraints: ResolvedConstraints,
    info: dict,
) -> ValidatedPlan:
    n = len(route)
    if n == 0:
        return ValidatedPlan(
            route=[],
            walk_seconds=0.0,
            visit_seconds=0,
            total_seconds=0,
            fits_budget=False,
            stops_dropped=0,
            trace={"error": "empty_route"},
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

    walk = sum(matrix[a][b] for a, b in zip(order, order[1:])) if n >= 2 else 0.0
    visits_s = sum(visits[i] for i in order) * 60
    total = int(walk) + int(visits_s)

    budget_s = constraints.time_budget_minutes * 60
    fits = total <= budget_s if budget_s > 0 else True

    # Diversity: unique categories / n.
    cats = {c.category for c in route if c.category}
    diversity = len(cats) / n if n else 0.0

    # Longest single walking leg.
    max_leg = max(
        (matrix[a][b] for a, b in zip(order, order[1:])), default=0.0
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
    }

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

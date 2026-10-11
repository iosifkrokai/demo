"""Coverage gate: refuse a request whose named place is outside the region."""

from __future__ import annotations

import time as _time
from typing import Any

from core import constants
from planner.models import (
    CostMatrix,
    GenerateReq,
    ResolvedConstraints,
    RouteResponse,
)
from telemetry import trace

from .intent import mark_out_of_coverage
from .response import _verdicts, build_response
from .validate import validate
from .verify import overall_status


def _outside_left_unresolved(requirements: Any, constraints: ResolvedConstraints) -> list[str]:
    """Names the reading placed outside the region that stayed unresolvable.

    A name that resolved to a place inside the region is not a refusal.
    """
    flagged = getattr(requirements, "outside_coverage", None) or []
    if not flagged:
        return []
    resolved = {
        (n or "").strip().lower() for n in (getattr(constraints, "resolved_names", None) or [])
    }
    return [n for n in flagged if (n or "").strip().lower() not in resolved]


def refuse_out_of_coverage(
    req: GenerateReq,
    requirements: Any,
    intent: Any,
    constraints: ResolvedConstraints,
    names: list[str],
    t0: float,
) -> RouteResponse:
    """Answer "not here" instead of planning a route somewhere else.

    No stop is returned: the verifier's own rule makes the status `infeasible`.
    """
    mark_out_of_coverage(requirements, names)
    plan = validate(
        [],
        CostMatrix(),
        constraints,
        {},
        requirements=requirements,
    )
    status = overall_status(requirements)
    trace.record(
        "verify",
        input=[r.code or r.text for r in requirements.requirements][:10],
        plan_status=status,
        output=_verdicts(requirements),
    )
    trace.record(
        "response",
        input={"plan_status": status, "refused": names},
        plan_status=status,
        stops=0,
        refused=names,
        ms=int((_time.perf_counter() - t0) * 1000),
    )
    return build_response(
        intent=intent,
        changes=None,
        constraints=constraints,
        plan=plan,
        shape={},
        walk_s=0.0,
        length_km=0.0,
        explanation=(
            "Маршрут не построен: "
            + ", ".join(names)
            + " — вне зоны покрытия (Гродненская область)."
        ),
        costing=req.profile or "pedestrian",
        requirements=requirements,
        status=status,
        deadline={
            "budget_s": constants.REQUEST_DEADLINE_S,
            "used_s": round(_time.perf_counter() - t0, 3),
            "pool_trimmed_to": None,
            "valhalla_order_skipped": False,
            "geometry_skipped": False,
        },
    )

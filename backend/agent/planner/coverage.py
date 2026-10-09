"""Coverage gate: refuse a request whose named place is outside the region.

The reading can say that a name in the request lies outside the region this
system serves; what follows from that is decided here, before anything is
retrieved.  A refusal is the honest answer; a route to somewhere else is not.
"""

from __future__ import annotations

import time as _time
from typing import Any

from contracts.planner import (
    CostMatrix,
    GenerateReq,
    ResolvedConstraints,
    RouteResponse,
)
from domain import constants
from infra import trace

from .intent import mark_out_of_coverage
from .response import _verdicts
from .validate import validate
from .verify import overall_status


def _outside_left_unresolved(
    requirements: Any, constraints: ResolvedConstraints
) -> list[str]:
    """Names the reading placed outside the region that stayed unresolvable.

    The cross-check that keeps a wrong reading harmless: if the name DID resolve
    to a place inside the region — the model flagged «Старый замок» although it
    is in Grodno — it is not a refusal, whatever the reading said. Only a name
    that has no place here at all (Vilnius Cathedral) is left in the list.
    """
    flagged = getattr(requirements, "outside_coverage", None) or []
    if not flagged:
        return []
    resolved = {
        (n or "").strip().lower()
        for n in (getattr(constraints, "resolved_names", None) or [])
    }
    return [n for n in flagged if (n or "").strip().lower() not in resolved]


def refuse_out_of_coverage(
    pipeline: Any,
    req: GenerateReq,
    requirements: Any,
    intent: Any,
    constraints: ResolvedConstraints,
    names: list[str],
    t0: float,
) -> RouteResponse:
    """Answer "not here" instead of planning a route somewhere else.

    No stop is returned, and the reason is not prose invented for the client:
    the contract carries a hard requirement that nothing in this region can
    satisfy, so the verifier's own rule makes the status `infeasible` and the
    client localises the reason code. Building a plan out of look-alikes would
    look more complete and be worse — it would walk a tourist to another
    town's landmarks under the name they asked for.
    """
    mark_out_of_coverage(requirements, names)
    # No stops at all: an empty matrix is what validate() expects here (it
    # returns before touching it), and the costing is reported even though
    # nothing was planned.
    plan = validate(
        [],
        CostMatrix(),
        constraints,
        {},
        requirements=requirements,
    )
    # A refusal is a step like any other: the trace says which requirements
    # were judged unmet and why, so «почему отказ» is answered by evidence
    # rather than by the sentence the client is shown.
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
    return pipeline._build_response(
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

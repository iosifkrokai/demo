"""cost → optimize → validate: the tail all three planning entry points share.

`generate`, `reroute` and a refinement turn all end here, and `turn.tail_mode`
says which of the three they are — a refinement measures what it has, a reroute
orders a fixed list, and a fresh generate searches, retries on a collapsed
route, re-orders through Valhalla when there is time, and prunes what cannot be
reached.

It was a method on `Pipeline`, but it used `self` for exactly one thing: the
clock. It is a function over a `Turn` now, which is what it always was.
"""

from __future__ import annotations

import logging
from typing import Any

from contracts.planner import Candidate, ResolvedConstraints
from core import constants
from core.errors import NoRoutePossible
from telemetry import progress, trace

from .cost import _build_cost, _is_sight_stop, _refinement_cost, compute_cost_matrix
from .geo import _TRACE_NAMES_MAX
from .optimize import _order_after_prune, _prune_unroutable, _valhalla_order, optimize
from .response import _gone, _names
from .turn import Turn, seconds_left
from .validate import validate

log = logging.getLogger(__name__)


def run_plan_tail(
    turn: Turn,
    candidates: list[Candidate],
    constraints: ResolvedConstraints,
):
    """The shared tail. Returns ``(candidates, cost, route, info, plan)``.

    How the pool is costed, and whether an order is searched, is ``turn.tail_mode``.
    """
    costing = turn.costing

    if turn.tail_mode == "refine":
        route, cost = _refinement_cost(candidates, constraints, costing)
        info = turn.plan_info or {}
        plan = validate(route, cost, constraints, info)
        return candidates, cost, route, info, plan

    if turn.tail_mode == "reroute":
        cost = compute_cost_matrix(candidates, constraints, costing=costing)
        if cost.indices != list(range(len(candidates))):
            candidates = [candidates[i] for i in cost.indices]
        route, info = optimize(candidates, cost, constraints, costing=costing)
        plan = validate(route, cost, constraints, info)
        return candidates, cost, route, info, plan

    progress.note(progress.STAGE_MEASURING_LEGS)
    before_cost = candidates
    candidates, cost = _build_cost(candidates, constraints, costing)
    trace.record(
        "cost",
        input=_names(before_cost),
        before=len(before_cost),
        candidates=len(candidates),
        costing=costing,
        unreachable=_gone(before_cost, candidates),
    )

    progress.note(progress.STAGE_ORDERING)
    route, info = optimize(candidates, cost, constraints, costing=costing)
    by_id = {c.id: c.name for c in candidates}
    trace.record(
        "optimize",
        input={"candidates": _names(candidates), "costing": costing},
        stops=len(route),
        costing=costing,
        route=[c.name for c in route],
        algorithm=info.get("algorithm"),
        iterations=info.get("iterations"),
        must_missing=[
            by_id.get(i, str(i))
            for i in info.get("missing_must_visit_ids") or []
        ][:_TRACE_NAMES_MAX],
    )

    # A route that collapsed to a single stop is retried twice before giving up:
    # first on the sights alone, then on the wider pool that includes services.
    retry: dict[str, Any] | None = None
    if len(route) < 2 and len(candidates) > 1:
        sights = [c for c in candidates if _is_sight_stop(c)]
        if 2 <= len(sights) < len(candidates):
            log.info(
                "optimize collapsed to %d stop(s) — retrying on %d sight stop(s)",
                len(route), len(sights),
            )
            retry_candidates, retry_cost = _build_cost(sights, constraints, costing)
            retry_route, retry_info = optimize(
                retry_candidates, retry_cost, constraints, costing=costing
            )
            if len(retry_route) >= 2:
                candidates, cost, route, info = (
                    retry_candidates, retry_cost, retry_route, retry_info,
                )
                retry = {"why": "walkable", "pool": _names(sights)}
    if len(route) < 2 and len(turn.all_candidates) > len(candidates):
        log.info("optimize: sights alone give %d stop(s) — retrying with services", len(route))
        retry_candidates, retry_cost = _build_cost(turn.all_candidates, constraints, costing)
        retry_route, retry_info = optimize(
            retry_candidates, retry_cost, constraints, costing=costing
        )
        if len(retry_route) >= 2:
            candidates, cost, route, info = (
                retry_candidates, retry_cost, retry_route, retry_info,
            )
            retry = {"why": "services_back", "pool": _names(turn.all_candidates)}
    if retry is not None:
        trace.record(
            "optimize · retry",
            input=retry["pool"],
            why=retry["why"],
            stops=len(route),
            algorithm=info.get("algorithm"),
        )
    if len(route) < 2:
        raise NoRoutePossible("optimizer could not produce a route with ≥ 2 stops")

    matrix_order = [c.name for c in route]
    if seconds_left(turn.t0) >= constants.VALHALLA_ORDER_MIN_LEFT_S:
        route, info = _valhalla_order(
            route,
            info,
            costing=costing,
            timeout=constants.VALHALLA_ORDER_TIMEOUT_S,
            retries=constants.VALHALLA_ORDER_RETRIES,
        )
    else:
        log.info(
            "deadline: %.1fs left — skipping Valhalla re-ordering",
            seconds_left(turn.t0),
        )
        turn.deadline_order_skipped = True
    trace.record(
        "order",
        "skipped" if turn.deadline_order_skipped else "ok",
        input=matrix_order,
        skipped=turn.deadline_order_skipped,
        before=matrix_order,
        route=[c.name for c in route],
    )

    planned = len(route)
    route, prune_report = _prune_unroutable(
        route, candidates, cost, constraints.must_visit_ids
    )
    info = _order_after_prune(info, route, candidates)
    trace.record(
        "prune",
        input=[c.name for c in route],
        stops=len(route),
        before=planned,
        dropped=planned - len(route),
        unroutable=[c.name for c in prune_report],
    )

    plan = validate(route, cost, constraints, info, prune_report=prune_report)
    trace.record(
        "validate",
        input=[c.name for c in route],
        stops=len(plan.route),
        fits_budget=plan.trace.get("fits_budget"),
        stops_dropped=plan.stops_dropped,
        dropped=(info.get("stops_dropped_names") or [])[:_TRACE_NAMES_MAX],
        walk_seconds=plan.trace.get("walk_seconds"),
        visit_seconds=plan.trace.get("visit_seconds"),
        total_seconds=plan.trace.get("total_seconds"),
        budget_seconds=plan.trace.get("budget_seconds"),
        max_leg_seconds=plan.trace.get("max_leg_seconds"),
        budget_exceeded=plan.trace.get("budget_exceeded"),
        stop_categories=plan.trace.get("categories"),
        diversity=plan.trace.get("diversity"),
    )
    return candidates, cost, route, info, plan

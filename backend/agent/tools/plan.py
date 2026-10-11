"""``plan_walk`` — order the chosen stops into a walk and measure it.

This is the one tool the agent cannot get by without, and the one that must never
do arithmetic in the model's head: the cost matrix, the ordering, the budget
pruning and the drawn line are all decided here, by code, and handed back as
numbers. Six steps of the old pipeline collapse into this one call.

`must_keep_ids` are stops the user already has: they are protected from pruning,
which is what used to make a refinement turn unable to answer 422.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from agent.schema import PlanningDeps
from agent.tools import (
    ERR_BAD_ARGUMENT,
    ERR_DB_UNAVAILABLE,
    Remember,
    ToolAnswer,
    envelope,
    fetch,
)
from core import constants
from core.errors import NoRoutePossibleError, UpstreamUnavailableError
from routing.cost import build_cost, refinement_cost
from routing.models import CategoryFacts, LatLon, ResolvedConstraints
from routing.optimize import optimize, order_after_prune, prune_unroutable, valhalla_order
from routing.render import render
from routing.stops import candidate_of
from routing.validate import validate

MAX_STOPS_PER_CALL = constants.VALHALLA_MAX_LOCATIONS


def _facts(repos: Any, facts: CategoryFacts | None) -> CategoryFacts:
    """The taxonomy as a value — built from the repository when not handed one."""
    if facts is not None:
        return facts
    return CategoryFacts.from_rows(repos.taxonomy.all())


def _stop(card: Any, visit_minutes: int) -> dict[str, Any]:
    return {
        "id": card.id,
        "name": card.name,
        "category": card.category,
        "town": card.town,
        "lat": card.lat,
        "lon": card.lon,
        "visit_minutes": visit_minutes,
    }


def plan_walk(
    stop_ids: list[int],
    *,
    repos: Any,
    budget_minutes: int | None = None,
    profile: str = "pedestrian",
    origin: LatLon | None = None,
    round_trip: bool = False,
    must_keep_ids: list[int] | None = None,
    locale: str = "ru",
    facts: CategoryFacts | None = None,
    deadline_s: float | None = None,
) -> dict[str, Any]:
    """Order and measure. Never raises: a walk that cannot be built is reported."""
    provenance: dict[str, Any] = {
        "source": "places+valhalla",
        "profile": profile,
        "round_trip": round_trip,
        "budget_minutes": budget_minutes,
        "result_cap": MAX_STOPS_PER_CALL,
    }

    wanted = [i for i in (stop_ids or []) if i > 0]
    if len(wanted) < 2:
        return envelope(
            "plan_walk",
            [],
            provenance,
            error=ERR_BAD_ARGUMENT,
            message="at least two stop ids are needed to build a walk",
        )
    if len(wanted) > MAX_STOPS_PER_CALL:
        wanted = wanted[:MAX_STOPS_PER_CALL]
        provenance["capped"] = True

    rows, error, message = fetch(repos, lambda store: store.places.get_by_ids(wanted))
    if error is not None:
        return envelope("plan_walk", [], provenance, error=error, message=message)

    by_id = {row.id: row for row in (rows or [])}
    missing = [i for i in wanted if i not in by_id]
    candidates = [candidate_of(by_id[i], 0.0) for i in wanted if i in by_id]
    if len(candidates) < 2:
        return envelope(
            "plan_walk",
            [],
            provenance,
            error=ERR_DB_UNAVAILABLE if missing else ERR_BAD_ARGUMENT,
            message=f"fewer than two of the given stops exist (missing: {missing})",
        )

    keep = [i for i in (must_keep_ids or []) if i in by_id]
    constraints = ResolvedConstraints(
        must_visit_ids=keep,
        time_budget_minutes=budget_minutes,
        round_trip=round_trip,
    )
    taxonomy = _facts(repos, facts)

    try:
        if keep:
            candidates, cost = refinement_cost(candidates, constraints, profile, taxonomy)
        else:
            candidates, cost = build_cost(candidates, constraints, profile, taxonomy)
        route, info = optimize(candidates, cost, constraints, costing=profile)

        if deadline_s is None or deadline_s >= constants.VALHALLA_ORDER_MIN_LEFT_S:
            route, info = valhalla_order(
                route,
                info,
                costing=profile,
                timeout=constants.VALHALLA_ORDER_TIMEOUT_S,
                retries=constants.VALHALLA_ORDER_RETRIES,
            )
        planned = len(route)
        route, prune_report = prune_unroutable(route, candidates, cost, constraints.must_visit_ids)
        info = order_after_prune(info, route, candidates)
        plan = validate(route, cost, constraints, info, prune_report=prune_report)
    except NoRoutePossibleError as exc:
        provenance["reason_code"] = "no_route_possible"
        return envelope("plan_walk", [], provenance, error="no_route", message=str(exc))

    shape: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    try:
        shape, summary, _status = render(
            route, costing=profile, origin=origin, locale=locale, round_trip=round_trip
        )
    except UpstreamUnavailableError:
        provenance["geometry"] = "valhalla unavailable"

    walk_seconds = float(summary.get("time") or plan.walk_seconds or 0.0)
    visits = {c.id: v for c, v in zip(candidates, cost.visit_minutes, strict=False)}
    stops = [_stop(card, visits.get(card.id) or 0) for card in plan.route]
    provenance["algorithm"] = info.get("algorithm")
    provenance["stops_dropped"] = plan.stops_dropped
    provenance["unroutable"] = [p.name for p in prune_report]
    provenance["missing_ids"] = missing

    return envelope(
        "plan_walk",
        stops,
        provenance,
        capped=bool(provenance.get("capped") or planned > len(plan.route)),
    ) | {
        "walk_minutes": round(walk_seconds / 60),
        "visit_minutes": plan.visit_seconds // 60,
        "total_minutes": plan.total_seconds // 60,
        "fits_budget": plan.fits_budget,
        "length_km": summary.get("length"),
        "shape": shape or None,
        "round_trip": round_trip,
    }


def register_plan_walk(agent: Any, _remember: Remember) -> None:
    """Advertise ``plan_walk`` to the agent."""

    @agent.tool(name="plan_walk")
    def _plan_walk(
        ctx: RunContext[PlanningDeps],
        stop_ids: list[int],
        budget_minutes: int | None = None,
        profile: str = "pedestrian",
        round_trip: bool = False,
        must_keep_ids: list[int] | None = None,
    ) -> ToolAnswer:
        """Order chosen stops into the shortest feasible walk and measure it.

        Returns the stops in walking order, the minutes of walking and visiting,
        whether the budget is met, and the drawn line. Nothing here is estimated:
        the numbers come from the routing engine and the budget arithmetic.
        """
        return plan_walk(
            stop_ids,
            repos=ctx.deps.repos,
            budget_minutes=budget_minutes,
            profile=profile,
            origin=ctx.deps.origin,
            round_trip=round_trip,
            must_keep_ids=must_keep_ids,
            locale=ctx.deps.locale,
            facts=ctx.deps.facts,
            deadline_s=ctx.deps.seconds_left(),
        )

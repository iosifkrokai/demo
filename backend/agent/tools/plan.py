"""``plan_walk`` — order the chosen stops into a walk and measure it."""

from __future__ import annotations

from dataclasses import replace
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
from core import cache, constants
from core.errors import NoRoutePossibleError, UpstreamUnavailableError
from db.store.errors import StorageUnavailableError
from routing.cost import build_cost, refinement_cost
from routing.models import CategoryFacts, LatLon, WalkProblem, WalkRequest
from routing.optimize import optimize, order_after_prune, prune_unroutable, valhalla_order
from routing.render import render
from routing.stops import candidate_of
from routing.valhalla.constants import costing_for
from routing.validate import validate

MAX_STOPS_PER_CALL = constants.VALHALLA_MAX_LOCATIONS


def _measure_key(request: WalkRequest, *, data_version: str, regime: str = "order") -> str:
    """The identity of one measurement: the stops, the profile and the budget."""
    origin = request.origin
    where = f"{origin.lat:.6f},{origin.lon:.6f}" if origin is not None else "-"
    keep = ",".join(str(i) for i in sorted(request.must_keep_ids or []))
    return "|".join(
        (
            "plan_walk",
            data_version,
            ",".join(str(i) for i in sorted(request.stop_ids)),
            costing_for(request.profile),
            str(request.budget_minutes),
            "rt" if request.round_trip else "one_way",
            where,
            request.locale,
            keep,
            regime,
        )
    )


def _order_regime(deadline_s: float | None, origin: LatLon | None) -> str:
    """Which ordering the run will end up with — part of what gets measured."""
    room = deadline_s is None or deadline_s >= constants.VALHALLA_ORDER_MIN_LEFT_S
    return "order" if room and origin is None else "noorder"


def _data_version(repos: Any) -> str:
    """The projection's content version, or "" when it cannot be read."""
    datasets = getattr(repos, "datasets", None)
    if datasets is None:
        return ""
    try:
        return datasets.version() or ""
    except StorageUnavailableError:
        return ""


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
    request: WalkRequest,
    *,
    repos: Any,
    facts: CategoryFacts | None = None,
    deadline_s: float | None = None,
) -> dict[str, Any]:
    """Order and measure. Never raises: a walk that cannot be built is reported."""
    costing = costing_for(request.profile)
    provenance: dict[str, Any] = {
        "source": "places+valhalla",
        "profile": costing,
        "round_trip": request.round_trip,
        "budget_minutes": request.budget_minutes,
        "result_cap": MAX_STOPS_PER_CALL,
    }

    wanted = [i for i in request.stop_ids if i > 0]
    if len(wanted) < constants.MIN_LEG_POINTS:
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
    request = replace(request, stop_ids=wanted)

    key = _measure_key(
        request,
        data_version=_data_version(repos),
        regime=_order_regime(deadline_s, request.origin),
    )
    cached = cache.MEASURE_CACHE.get(key)
    if cached is not None:
        return dict(cached)

    rows, error, message = fetch(repos, lambda store: store.places.get_by_ids(wanted))
    if error is not None:
        return envelope("plan_walk", [], provenance, error=error, message=message)

    by_id = {row.id: row for row in (rows or [])}
    missing = [i for i in wanted if i not in by_id]
    candidates = [candidate_of(by_id[i], 0.0) for i in wanted if i in by_id]
    if len(candidates) < constants.MIN_LEG_POINTS:
        return envelope(
            "plan_walk",
            [],
            provenance,
            error=ERR_DB_UNAVAILABLE if missing else ERR_BAD_ARGUMENT,
            message=f"fewer than two of the given stops exist (missing: {missing})",
        )

    keep = [i for i in (request.must_keep_ids or []) if i in by_id]
    constraints = request.constraints(keep=keep)
    taxonomy = _facts(repos, facts)
    info: dict[str, Any] = {}

    try:
        if keep:
            candidates, cost = refinement_cost(candidates, constraints, costing, taxonomy)
        else:
            candidates, cost = build_cost(candidates, constraints, costing, taxonomy)
        problem = WalkProblem(candidates, cost, constraints, costing)
        route, info = optimize(problem)

        if deadline_s is None or deadline_s >= constants.VALHALLA_ORDER_MIN_LEFT_S:
            route, info = valhalla_order(
                route,
                info,
                problem,
                timeout=constants.VALHALLA_ORDER_TIMEOUT_S,
                retries=constants.VALHALLA_ORDER_RETRIES,
            )
        planned = len(route)
        route, prune_report = prune_unroutable(route, candidates, cost, constraints.must_visit_ids)
        info = order_after_prune(info, route, candidates)
        plan = validate(route, cost, constraints, info, prune_report=prune_report)
    except NoRoutePossibleError as exc:
        provenance["reason_code"] = "no_route_possible"
        message = _no_route_message(info, request.origin, str(exc))
        return envelope("plan_walk", [], provenance, error="no_route", message=message)

    shape: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    try:
        shape, summary, _status = render(
            route,
            costing=costing,
            origin=request.origin,
            locale=request.locale,
            round_trip=request.round_trip,
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

    answer = envelope(
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
        "round_trip": request.round_trip,
    }
    if answer.get("error") is None and answer.get("results"):
        cache.MEASURE_CACHE.put(key, answer)
    return answer


def _no_route_message(info: dict[str, Any], origin: LatLon | None, fallback: str) -> str:
    """Say why the walk could not be built, without blaming the engine for it."""
    if not info.get("stops_dropped"):
        return fallback
    where = " от точки старта" if origin is not None else ""
    return (
        f"Маршрут не влезает в отведённое время: даже без части остановок "
        f"не хватает бюджета на дорогу{where}. Увеличьте время или начните ближе."
    )


def register_plan_walk(agent: Any, _remember: Remember) -> None:
    """Advertise ``plan_walk`` to the agent."""

    @agent.tool(name="plan_walk")
    def _plan_walk(
        ctx: RunContext[PlanningDeps],
        stop_ids: list[int],
        *,
        budget_minutes: int | None = None,
        profile: str = "pedestrian",
        round_trip: bool = False,
        must_keep_ids: list[int] | None = None,
    ) -> ToolAnswer:
        """Order chosen stops into the shortest feasible walk and measure it."""
        return plan_walk(
            WalkRequest(
                stop_ids=stop_ids,
                profile=profile,
                budget_minutes=budget_minutes,
                origin=ctx.deps.origin,
                round_trip=round_trip,
                must_keep_ids=must_keep_ids,
                locale=ctx.deps.locale,
            ),
            repos=ctx.deps.repos,
            facts=ctx.deps.facts,
            deadline_s=ctx.deps.seconds_left(),
        )

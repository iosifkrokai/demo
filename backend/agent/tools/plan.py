"""``plan_walk`` — order the chosen stops into a walk and measure it."""

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
from core import cache, constants
from core.errors import NoRoutePossibleError, UpstreamUnavailableError
from db.store.errors import StorageUnavailableError
from routing.cost import build_cost, refinement_cost
from routing.models import CategoryFacts, LatLon, ResolvedConstraints
from routing.optimize import optimize, order_after_prune, prune_unroutable, valhalla_order
from routing.render import render
from routing.stops import candidate_of
from routing.valhalla.constants import costing_for
from routing.validate import validate

MAX_STOPS_PER_CALL = constants.VALHALLA_MAX_LOCATIONS


def _measure_key(
    wanted: list[int],
    budget_minutes: int | None,
    profile: str,
    round_trip: bool,
    origin: LatLon | None,
    *,
    locale: str,
    must_keep_ids: list[int] | None = None,
    data_version: str,
    regime: str = "order",
) -> str:
    """The identity of one measurement: the stops, the profile and the budget."""
    where = f"{origin.lat:.6f},{origin.lon:.6f}" if origin is not None else "-"
    keep = ",".join(str(i) for i in sorted(must_keep_ids or []))
    return "|".join(
        (
            "plan_walk",
            data_version,
            ",".join(str(i) for i in sorted(wanted)),
            profile or "",
            str(budget_minutes),
            "rt" if round_trip else "one_way",
            where,
            locale,
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
    costing = costing_for(profile)
    provenance: dict[str, Any] = {
        "source": "places+valhalla",
        "profile": costing,
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

    key = _measure_key(
        wanted,
        budget_minutes,
        costing,
        round_trip,
        origin,
        locale=locale,
        must_keep_ids=must_keep_ids,
        data_version=_data_version(repos),
        regime=_order_regime(deadline_s, origin),
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
        origin=origin,
    )
    taxonomy = _facts(repos, facts)
    info: dict[str, Any] = {}

    try:
        if keep:
            candidates, cost = refinement_cost(candidates, constraints, costing, taxonomy)
        else:
            candidates, cost = build_cost(candidates, constraints, costing, taxonomy)
        route, info = optimize(candidates, cost, constraints, costing=costing)

        if deadline_s is None or deadline_s >= constants.VALHALLA_ORDER_MIN_LEFT_S:
            route, info = valhalla_order(
                route,
                info,
                costing=costing,
                timeout=constants.VALHALLA_ORDER_TIMEOUT_S,
                retries=constants.VALHALLA_ORDER_RETRIES,
                anchor=cost.anchor,
            )
        planned = len(route)
        route, prune_report = prune_unroutable(route, candidates, cost, constraints.must_visit_ids)
        info = order_after_prune(info, route, candidates)
        plan = validate(route, cost, constraints, info, prune_report=prune_report)
    except NoRoutePossibleError as exc:
        provenance["reason_code"] = "no_route_possible"
        message = _no_route_message(info, origin, str(exc))
        return envelope("plan_walk", [], provenance, error="no_route", message=message)

    shape: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    try:
        shape, summary, _status = render(
            route, costing=costing, origin=origin, locale=locale, round_trip=round_trip
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
        "round_trip": round_trip,
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

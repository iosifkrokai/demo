"""``plan_walk`` — order the chosen stops into a walk and measure it."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
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
from routing.cost import build_cost, refinement_cost, sub_matrix
from routing.models import (
    Candidate,
    CategoryFacts,
    CostMatrix,
    LatLon,
    ResolvedConstraints,
    WalkProblem,
    WalkRequest,
)
from routing.optimize import optimize, order_after_prune, prune_unroutable, valhalla_order
from routing.render import render
from routing.stops import candidate_of
from routing.valhalla.constants import costing_for, costing_options_for
from routing.valhalla.types import RouteStatus
from routing.validate import validate

MAX_STOPS_PER_CALL = constants.VALHALLA_MAX_LOCATIONS

log = logging.getLogger(__name__)


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
            "steps" if request.step_free else "any_step",
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
        "opening_hours": card.opening_hours,
        "ticket_price": card.ticket_price,
    }


def _stops_and_services(
    candidates: list[Candidate], taxonomy: CategoryFacts, keep: set[int]
) -> tuple[list[Candidate], list[Candidate]]:
    """Split the candidates into the stops of the walk and the services beside it.

    A service is a place the walk rests at, not a sight of it, so it is not a stop —
    unless the request pinned it by id, which is a person saying they want to go there.
    """
    stops: list[Candidate] = []
    services: list[Candidate] = []
    for candidate in candidates:
        if candidate.id not in keep and taxonomy.is_service(candidate.category):
            services.append(candidate)
        else:
            stops.append(candidate)
    return stops, services


@dataclass(frozen=True)
class MeasureSetup:
    """What a measurement needs besides the stops and the request itself."""

    repos: Any
    provenance: dict[str, Any]
    facts: CategoryFacts | None = None
    preserve_order: bool = False
    deadline_s: float | None = None


def measure_candidates(
    candidates: list[Candidate],
    request: WalkRequest,
    setup: MeasureSetup,
) -> dict[str, Any]:
    """Order — unless told not to — and measure stops the caller already holds.

    Takes stops rather than ids, because a client holds points that are not in the
    catalogue. `preserve_order` makes its order the answer: nothing reorders it and
    nothing is dropped to fit a budget — a walk that no longer fits says so instead.
    A service — a café, a hotel — is where the walk passes, not where it stops: it is
    dropped here unless the request asked for it by name, and the drop is reported.
    """
    costing = costing_for(request.profile)
    options = costing_options_for(request.profile, request.step_free)
    provenance = setup.provenance
    present = {c.id for c in candidates}
    missing = [i for i in request.stop_ids if i not in present]
    taxonomy = _facts(setup.repos, setup.facts)
    candidates, services = _stops_and_services(
        candidates, taxonomy, keep={*(request.must_keep_ids or [])}
    )
    keep = [i for i in (request.must_keep_ids or []) if i in {c.id for c in candidates}]
    constraints = request.constraints(keep=keep)
    if services:
        provenance["services_not_stops"] = [c.name for c in services]
    info: dict[str, Any] = {}

    try:
        if keep:
            candidates, cost = refinement_cost(
                candidates, constraints, costing, taxonomy, costing_options=options
            )
        else:
            candidates, cost = build_cost(
                candidates, constraints, costing, taxonomy, costing_options=options
            )
        problem = WalkProblem(candidates, cost, constraints, costing)
        if setup.preserve_order:
            info = {"algorithm": "as_sent", "order": list(range(len(candidates)))}
            route = list(candidates)
        else:
            route, info = optimize(problem)

        if not setup.preserve_order and (
            setup.deadline_s is None or setup.deadline_s >= constants.VALHALLA_ORDER_MIN_LEFT_S
        ):
            route, info = valhalla_order(
                route,
                info,
                problem,
                timeout=constants.VALHALLA_ORDER_TIMEOUT_S,
                retries=constants.VALHALLA_ORDER_RETRIES,
                costing_options=options,
            )
        planned = len(route)
        route, prune_report = prune_unroutable(route, candidates, cost, constraints.must_visit_ids)
        if prune_report:
            route, info = _reorder_after_prune(route, candidates, cost, constraints, info)
            route, again = prune_unroutable(route, candidates, cost, constraints.must_visit_ids)
            if again:
                info = order_after_prune(info, route, candidates)
            prune_report = [*prune_report, *again]
        else:
            info = order_after_prune(info, route, candidates)
        plan = validate(route, cost, constraints, info, prune_report=prune_report)
    except NoRoutePossibleError as exc:
        provenance["reason_code"] = "no_route_possible"
        message = (
            _services_only_message(services)
            if services and len(candidates) < constants.MIN_LEG_POINTS
            else _no_route_message(info, request.origin, str(exc))
        )
        return envelope("plan_walk", [], provenance, error="no_route", message=message)

    shape, summary = _drawn(route, request, costing, provenance, options)
    visits = {c.id: v for c, v in zip(candidates, cost.visit_minutes, strict=False)}
    stops = [_stop(card, visits.get(card.id) or 0) for card in plan.route]
    provenance["algorithm"] = info.get("algorithm")
    provenance["stops_dropped"] = plan.stops_dropped
    provenance["unroutable"] = [p.name for p in prune_report]
    provenance["missing_ids"] = missing
    provenance["rendered_walk_seconds"] = summary.get("time")

    return envelope(
        "plan_walk",
        stops,
        provenance,
        capped=bool(provenance.get("capped") or planned > len(plan.route)),
    ) | {
        "walk_minutes": round(plan.walk_seconds / 60),
        "visit_minutes": plan.visit_seconds // 60,
        "total_minutes": plan.total_seconds // 60,
        "fits_budget": plan.fits_budget,
        "length_km": summary.get("length"),
        "shape": shape or None,
        "round_trip": request.round_trip,
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
        "step_free": request.step_free,
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

    answer = measure_candidates(
        candidates,
        request,
        MeasureSetup(repos=repos, provenance=provenance, facts=facts, deadline_s=deadline_s),
    )
    if request.step_free and answer.get("error") is None and answer.get("results"):
        baseline = measure_candidates(
            candidates,
            replace(request, step_free=False),
            MeasureSetup(repos=repos, provenance={}, facts=facts, deadline_s=deadline_s),
        )
        if baseline.get("error") is None and baseline.get("results"):
            answer["step_free"] = _step_free_price(answer, baseline)
    if answer.get("error") is None and answer.get("results"):
        cache.MEASURE_CACHE.put(key, answer)
    return answer


def _step_free_price(step_free: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """What the step-free walk cost, measured against the same stops taken directly."""
    here = int(step_free.get("walk_minutes") or 0)
    plain = int(baseline.get("walk_minutes") or 0)
    return {
        "requested": True,
        "walk_minutes": here,
        "without_steps_minutes": plain,
        "detour_minutes": max(0, here - plain),
        "length_km": step_free.get("length_km"),
        "without_steps_length_km": baseline.get("length_km"),
    }


def _reorder_after_prune(
    route: list[Candidate],
    candidates: list[Candidate],
    cost: CostMatrix,
    constraints: ResolvedConstraints,
    info: dict[str, Any],
) -> tuple[list[Candidate], dict[str, Any]]:
    """Order the survivors again: the walk the dropped stop was part of is not this one."""
    position = {id(card): i for i, card in enumerate(candidates)}
    idx = [position[id(card)] for card in route]
    if len(idx) < constants.MIN_TOUR_STOPS:
        return route, order_after_prune(info, route, candidates)
    ordered, fresh = optimize(
        WalkProblem(candidates=route, cost=sub_matrix(cost, idx), constraints=constraints)
    )
    return ordered, {
        **info,
        **fresh,
        "order": [idx[i] for i in fresh["order"]],
        "reordered_after_prune": True,
    }


def _drawn(
    route: list[Any],
    request: WalkRequest,
    costing: str,
    provenance: dict[str, Any],
    costing_options: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask Valhalla to draw the walk; a service that cannot is not a failed plan."""
    try:
        shape, summary, status = render(
            route,
            costing=costing,
            origin=request.origin,
            locale=request.locale,
            round_trip=request.round_trip,
            costing_options=costing_options,
        )
    except UpstreamUnavailableError:
        provenance["geometry"] = "valhalla unavailable"
        return {}, {}
    if status == RouteStatus.PARTIAL.value:
        provenance["geometry"] = "a leg is missing"
        log.warning("plan_walk: the drawn line is missing a leg")
    return shape, summary


def _services_only_message(services: list[Candidate]) -> str:
    """Why a request of nothing but services has no walk — the request, not the engine."""
    shown = services[:3]
    names = ", ".join(c.name for c in shown)
    tail = " и другие" if len(shown) < len(services) else ""
    return (
        "Остановками прогулки не становятся кафе, гостиницы и подобное — сервис это то, "
        f"мимо чего идём. Осмотр здесь не из чего собрать: {names}{tail}. "
        "Добавьте достопримечательность."
    )


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
    def _plan_walk(  # noqa: PLR0913 — the tool's whole surface, stops plus the levers
        ctx: RunContext[PlanningDeps],
        stop_ids: list[int],
        *,
        budget_minutes: int | None = None,
        profile: str = "pedestrian",
        round_trip: bool = False,
        must_keep_ids: list[int] | None = None,
        step_free: bool = False,
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
                step_free=step_free,
            ),
            repos=ctx.deps.repos,
            facts=ctx.deps.facts,
            deadline_s=ctx.deps.seconds_left(),
        )

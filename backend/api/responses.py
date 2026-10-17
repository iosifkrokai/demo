"""Turning a run's outcome into the wire."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from fastapi import HTTPException

from agent.client import AgentOutcome
from agent.models import BriefStop, ReaderBrief, RefinementBrief, TripRequirements
from agent.tools.plan import MeasureSetup, measure_candidates
from api.models.routes import (
    BudgetInfo,
    GenerateReq,
    Interpretation,
    OverallStatus,
    ParsedQuery,
    Place,
    PlannedAlternative,
    RequirementSignal,
    RerouteReq,
    RerouteStop,
    RouteChange,
    RouteChanges,
    RouteResponse,
    RouteSummary,
    StepFreeInfo,
)
from core import constants
from core.errors import NoRoutePossibleError
from routing.cost import visit_time_minutes
from routing.models import Candidate, CategoryFacts, WalkRequest
from routing.stops import candidate_of
from routing.valhalla.constants import costing_for

log = logging.getLogger(__name__)

PLACED_STOP_NAME = "Точка на карте"


def budget_info(
    measured: dict[str, Any], *, budget_minutes: int | None, stops_dropped: int = 0
) -> BudgetInfo:
    """The walk, the visits and their sum: one currency, so a panel that shows a
    sum shows a sum that comes out.
    """
    walk_minutes = int(measured.get("walk_minutes") or 0)
    visit_minutes = int(measured.get("visit_minutes") or 0)
    return BudgetInfo(
        budget_minutes=budget_minutes,
        walk_minutes=walk_minutes,
        visit_minutes=visit_minutes,
        total_minutes=walk_minutes + visit_minutes,
        fits=bool(measured.get("fits_budget")),
        stops_dropped=stops_dropped,
    )


def to_places(
    candidates: list[Candidate], facts: CategoryFacts, keys: dict[int, str] | None = None
) -> list[Place]:
    """Candidates → the response points (one mapping, used by every branch).
    `keys` names them the way the client that sent the route named them.
    """
    named = keys or {}
    return [
        Place(
            id=p.id,
            key=named.get(p.id),
            name=p.name,
            category=p.category,
            lat=p.lat,
            lon=p.lon,
            blurb=p.blurb,
            fun_fact=p.fun_fact,
            fun_facts=p.fun_facts,
            links=p.links,
            opening_hours=p.opening_hours,
            ticket_price=p.ticket_price,
            is_free=p.is_free,
            town=p.town,
            district=p.district,
            photo=p.photo,
            visit_minutes=p.visit_minutes_db or visit_time_minutes(p.category, facts),
        )
        for p in candidates
    ]


def route_changes(previous: Sequence[BriefStop], points: list[Place]) -> RouteChanges | None:
    """What a refinement did to the route it started from."""
    if not previous:
        return None
    new_ids = {place.id for place in points}
    prev_ids = {stop.place_id for stop in previous if stop.place_id is not None}
    new_by_name = {(place.name or "").strip(): place.id for place in points}
    named_kept: set[int] = set()
    removed: list[RouteChange] = []
    kept = 0
    for stop in previous:
        if stop.place_id is not None:
            if stop.place_id in new_ids:
                kept += 1
            else:
                removed.append(RouteChange(id=stop.place_id, name=stop.name))
        else:
            hit = new_by_name.get((stop.name or "").strip())
            if hit is not None:
                named_kept.add(hit)
                kept += 1
            else:
                removed.append(RouteChange(id=None, name=stop.name))
    added = [
        RouteChange(id=place.id, name=place.name)
        for place in points
        if place.id not in prev_ids and place.id not in named_kept
    ]
    return RouteChanges(added=added, removed=removed, kept=kept)


def _ordered_candidates(repos: Any, outcome: AgentOutcome) -> list[Candidate]:
    """The route as measured, in walking order, as full rows."""
    order = [card["id"] for card in outcome.measured.get("results") or []]
    if not order:
        return []
    rows = repos.places.get_by_ids(order) if repos is not None else []
    rank = {pid: index for index, pid in enumerate(order)}
    rows.sort(key=lambda row: rank.get(row.id or 0, len(rank)))
    return [candidate_of(row, 0.0) for row in rows]


OVERALL_STATUSES: dict[str, OverallStatus] = {
    "ready": "ready",
    "infeasible": "infeasible",
    "degraded": "degraded",
    "pending": "pending",
}


def interpretation(contract: TripRequirements | None, status: str | None) -> Interpretation | None:
    """What the system understood, in one compact block for the client."""
    if contract is None:
        return None

    def signal(r: Any) -> RequirementSignal:
        return RequirementSignal(
            kind=r.kind,
            strength=r.strength,
            code=r.code,
            name=r.name,
            origin="ui" if r.source == "ui" else "agent",
            status=r.status,
            reason=r.reason,
            place_ids=list(r.place_ids),
        )

    signals = [signal(r) for r in contract.requirements]
    return Interpretation(
        source=contract.source,
        locale=contract.locale,
        status=OVERALL_STATUSES.get(status or "", "pending"),
        at=contract.at if contract.at_chosen else None,
        adults=contract.party.adults,
        children=contract.party.children,
        children_ages=list(contract.party.children_ages),
        mobility=list(contract.party.mobility),
        budget_minutes=contract.budget_minutes,
        areas=list(contract.areas),
        transport=contract.costing,
        result_mode=contract.result_mode,
        round_trip=contract.round_trip,
        requirements=signals,
        unmet=[s for s in signals if s.status != "satisfied"],
        unknowns=list(contract.unknowns),
    )


def alternatives_for(
    *, costing: str, walk_s: float, length_km: float | None
) -> list[PlannedAlternative]:
    """What to offer when the plan outgrows the profile the tourist chose."""
    if costing not in ("pedestrian", "bicycle"):
        return []
    far = (length_km or 0.0) >= constants.WALK_TOO_FAR_KM
    long = walk_s >= constants.WALK_TOO_LONG_MINUTES * 60
    if not (far or long):
        return []
    reason = "too_far_to_walk" if far else "too_long_to_walk"
    offers: list[PlannedAlternative] = []
    if costing == "pedestrian":
        offers.append(
            PlannedAlternative(
                costing="bicycle",
                reason=reason,
                note=(
                    "Пешком это далеко: на велосипеде маршрут проезжается целиком "
                    "и занимает куда меньше времени."
                ),
            )
        )
    offers.append(
        PlannedAlternative(
            costing="auto",
            reason=reason,
            note=(
                "На машине или такси: точки разбросаны далеко друг от друга, а "
                "часть пути можно проехать на автобусе или троллейбусе."
            ),
        )
    )
    return offers


_MODE_WORDS = {"bicycle": "на велосипеде", "auto": "на машине или такси"}


def alternatives_sentence(offers: list[PlannedAlternative], walk_s: float) -> str:
    """The tourist's version of the same news, in the request's language."""
    hours = walk_s / 3600.0
    span = f"{hours:.1f} ч" if hours >= 1 else f"{int(walk_s / 60)} мин"
    modes = ", ".join(_MODE_WORDS.get(o.costing, o.costing) for o in offers)
    return (
        f"Пешком это не прогулка: {span} в пути. "
        f"Варианты: {modes}; часть пути можно проехать на автобусе, маршрутке "
        f"или троллейбусе."
    )


def _span(minutes: int) -> str:
    """Minutes as the panel spells a duration."""
    hours, mins = divmod(int(minutes), 60)
    if not hours:
        return f"{mins} мин"
    return f"{hours} ч {mins} мин" if mins else f"{hours} ч"


def step_free_info(measured: dict[str, Any]) -> StepFreeInfo | None:
    """The measured price of a step-free detour, when one was asked for and taken."""
    raw = measured.get("step_free")
    if not isinstance(raw, dict):
        return None
    return StepFreeInfo(
        walk_minutes=int(raw.get("walk_minutes") or 0),
        without_steps_minutes=int(raw.get("without_steps_minutes") or 0),
        detour_minutes=int(raw.get("detour_minutes") or 0),
        length_km=raw.get("length_km"),
        without_steps_length_km=raw.get("without_steps_length_km"),
    )


def step_free_sentence(info: StepFreeInfo) -> str:
    """The detour's price in the request's language: a step-free claim is only honest
    with what the detour cost shown beside it.
    """
    here = _span(info.walk_minutes)
    plain = _span(info.without_steps_minutes)
    if info.length_km is not None and info.without_steps_length_km is not None:
        here = f"{here} / {info.length_km:.1f} км"
        plain = f"{plain} / {info.without_steps_length_km:.1f} км"
    return f"Без лестниц путь длиннее: около {here} в обход, против {plain} по обычному маршруту."


def route_response(
    outcome: AgentOutcome,
    *,
    repos: Any,
    facts: CategoryFacts,
    previous: Sequence[BriefStop] | None = None,
) -> RouteResponse:
    """The one place a run's outcome becomes the response body."""
    contract = outcome.requirements
    measured = outcome.measured

    if outcome.refused:
        return _refusal_response(outcome)
    if contract.result_mode == "catalogue":
        return _catalogue_response(outcome, repos=repos, facts=facts, previous=previous)

    route = _ordered_candidates(repos, outcome)

    budget = budget_info(
        measured,
        budget_minutes=contract.budget_minutes,
        stops_dropped=int((measured.get("provenance") or {}).get("stops_dropped") or 0),
    )
    walk_s = float(budget.walk_minutes) * 60
    length_km = measured.get("length_km")
    costing = costing_for(outcome.requirements.costing)
    narrative = outcome.submission.narrative or None

    offers = alternatives_for(costing=costing, walk_s=walk_s, length_km=length_km)
    if offers and narrative:
        narrative = f"{narrative}\n\n{alternatives_sentence(offers, walk_s)}"

    step_free = step_free_info(measured)
    if step_free is not None and step_free.detour_minutes > 0:
        sentence = step_free_sentence(step_free)
        narrative = f"{narrative}\n\n{sentence}" if narrative else sentence

    points = to_places(route, facts)

    return RouteResponse(
        parsed=ParsedQuery(
            categories=contract.interest_codes(),
            time_budget_minutes=contract.budget_minutes,
            source="agent",
        ),
        points=points,
        shape=measured.get("shape") or {},
        summary=RouteSummary(length_km=length_km, time_seconds=walk_s),
        result_mode=contract.result_mode,
        budget=budget,
        explanation=narrative,
        alternatives=offers or None,
        status=OVERALL_STATUSES.get(outcome.status, "pending"),
        requirements=contract.public_requirements(),
        interpretation=interpretation(contract, outcome.status),
        costing=costing,
        changes=route_changes(previous or (), points),
        step_free=step_free,
        debug={
            "requirements_source": contract.source,
            "problems": outcome.problems,
            "verdicts": measured.get("verdicts"),
        },
    )


DEFAULT_REFUSAL = (
    "Маршрут не построен: в запросе нет мест внутри обслуживаемого региона "
    "(Гродненская область). Уточните запрос."
)


def _refusal_response(outcome: AgentOutcome) -> RouteResponse:
    """The agent said the request cannot be served: infeasible, with its reason."""
    contract = outcome.requirements
    reason = (outcome.submission.refusal_reason or "").strip() or DEFAULT_REFUSAL
    outside = ", ".join(contract.outside_coverage)
    return RouteResponse(
        parsed=ParsedQuery(
            categories=contract.interest_codes(),
            time_budget_minutes=contract.budget_minutes,
            source="agent",
        ),
        points=[],
        shape={},
        summary=RouteSummary(length_km=None, time_seconds=None),
        result_mode=contract.result_mode,
        budget=None,
        explanation=f"{reason} ({outside})" if outside else reason,
        status="infeasible",
        requirements=contract.public_requirements(),
        interpretation=interpretation(contract, "infeasible"),
        costing=costing_for(outcome.requirements.costing),
        changes=None,
        debug={
            "reason": "refused",
            "problems": outcome.problems,
            "outside_coverage": list(contract.outside_coverage),
        },
    )


CATALOGUE_PLACES = "Каталог: {n} мест — выберите точки, и я построю по ним маршрут."


def _catalogue_response(
    outcome: AgentOutcome,
    *,
    repos: Any,
    facts: CategoryFacts,
    previous: Sequence[BriefStop] | None = None,
) -> RouteResponse:
    """A list to choose from: the places, no line, and no numbers nobody measured."""
    contract = outcome.requirements
    measured = outcome.measured
    points = to_places(_ordered_candidates(repos, outcome), facts)
    narrative = (outcome.submission.narrative or "").strip()
    return RouteResponse(
        parsed=ParsedQuery(
            categories=contract.interest_codes(),
            time_budget_minutes=contract.budget_minutes,
            source="agent",
        ),
        points=points,
        shape={},
        summary=RouteSummary(length_km=None, time_seconds=None),
        result_mode="catalogue",
        budget=None,
        explanation=narrative or CATALOGUE_PLACES.format(n=len(points)),
        status=OVERALL_STATUSES.get(outcome.status, "pending"),
        requirements=contract.public_requirements(),
        interpretation=interpretation(contract, outcome.status),
        costing=costing_for(contract.costing),
        changes=route_changes(previous or (), points),
        debug={
            "result_mode": "catalogue",
            "n_places": len(points),
            "capped": bool(measured.get("capped")),
            "requirements_source": contract.source,
            "problems": outcome.problems,
            "verdicts": measured.get("verdicts"),
        },
    )


def no_route_response(req: GenerateReq, detail: str) -> RouteResponse:
    """Turn "there is no walk here" into an answer instead of a failed request."""
    return RouteResponse(
        parsed=ParsedQuery(time_budget_minutes=req.time_budget_minutes, source="agent"),
        points=[],
        shape={},
        summary=RouteSummary(length_km=None, time_seconds=None),
        budget=None,
        explanation=(
            "Маршрут не построен: в этой зоне не нашлось остановок, между которыми "
            "можно пройти. Уточните запрос или расширьте район."
        ),
        status="infeasible",
        requirements=[],
        costing=costing_for(req.profile),
        changes=None,
        debug={"reason": "no_walkable_route", "detail": detail},
    )


def explain_points(candidates: list[Candidate], facts: CategoryFacts) -> str:
    """A short Russian description of a list of points, written from the data."""
    if not candidates:
        return "Точки не выбраны."
    places = to_places(candidates, facts)
    lines = [f"{index}. {place.name}" for index, place in enumerate(places, start=1)]
    total = sum(place.visit_minutes or 0 for place in places)
    return f"Маршрут из {len(places)} точек:\n" + "\n".join(lines) + f"\nОсмотр: около {total} мин."


def reader_brief(req: GenerateReq) -> ReaderBrief:
    """Narrow the wire to what the agent is told."""
    origin = (req.origin.lat, req.origin.lon) if req.origin is not None else None
    context = None
    if req.context is not None:
        context = RefinementBrief(
            instruction=req.context.instruction,
            revision=req.context.revision,
            excluded_ids=tuple(req.context.excluded_ids),
            base_points=tuple(
                BriefStop(place_id=point.id, name=point.name, pinned=point.pinned)
                for point in req.context.base_points
            ),
        )
    return ReaderBrief(
        locale=req.locale,
        profile=req.profile,
        at=req.at,
        origin=origin,
        time_budget_minutes=req.time_budget_minutes,
        party_adults=req.party_adults,
        party_children=req.party_children,
        party_children_ages=tuple(req.party_children_ages),
        mobility=tuple(req.mobility),
        hard_services=tuple(req.hard_services),
        interests=tuple(req.interests),
        avoid=tuple(req.avoid),
        result_mode=req.result_mode,
        round_trip=req.round_trip,
        context=context,
    )


def _placed_stop(stop: RerouteStop, index: int) -> Candidate:
    """A stop the client placed itself: no row, a point and nothing more, under a negative id.
    The catalogue numbers from one upwards; the wire refuses a stop with nowhere to be.
    """
    if stop.lat is None or stop.lon is None:
        raise HTTPException(status_code=422, detail={"reason": "stop_without_location"})
    return Candidate(
        id=-(index + 1),
        name=stop.name or PLACED_STOP_NAME,
        category=None,
        lat=stop.lat,
        lon=stop.lon,
    )


def _as_measured(candidates: list[Candidate], measured: dict[str, Any]) -> list[Candidate]:
    """The candidates in the order the measurement settled on."""
    by_id = {c.id: c for c in candidates}
    return [by_id[card["id"]] for card in measured.get("results") or [] if card["id"] in by_id]


def reroute_response(req: RerouteReq, *, repos: Any) -> RouteResponse:
    """Measure and draw the route the client is holding, without asking the model.
    Editing a stop is a measurement, not a request to be interpreted.
    """
    stops = req.stops
    wanted = [stop.place_id for stop in stops if stop.place_id is not None]
    rows = repos.places.get_by_ids(wanted) if wanted else []
    found = {row.id: row for row in rows}
    missing = [pid for pid in wanted if pid not in found]
    if missing:
        raise NoRoutePossibleError(f"unknown stop ids: {sorted(missing)}")

    keys: dict[int, str] = {}
    candidates: list[Candidate] = []
    for index, stop in enumerate(stops):
        card = (
            candidate_of(found[stop.place_id], 0.0)
            if stop.place_id is not None
            else _placed_stop(stop, index)
        )
        keys[card.id] = stop.key
        candidates.append(card)

    facts = CategoryFacts.from_rows(repos.taxonomy.all())
    costing = costing_for(req.profile)
    provenance: dict[str, Any] = {
        "source": "places+valhalla",
        "profile": costing,
        "round_trip": req.round_trip,
        "budget_minutes": req.time_budget_minutes,
        "result_cap": constants.VALHALLA_MAX_LOCATIONS,
    }
    measured = measure_candidates(
        candidates,
        WalkRequest(
            stop_ids=wanted,
            profile=req.profile or "pedestrian",
            budget_minutes=req.time_budget_minutes,
            origin=req.origin,
            round_trip=req.round_trip,
            must_keep_ids=[c.id for c in candidates if facts.is_service(c.category)] or None,
        ),
        MeasureSetup(
            repos=repos, provenance=provenance, facts=facts, preserve_order=req.preserve_order
        ),
    )
    if measured.get("error") is not None:
        raise NoRoutePossibleError(str(measured.get("message") or measured["error"]))

    route = _as_measured(candidates, measured)
    walk_s = float(measured.get("walk_minutes") or 0) * 60
    return RouteResponse(
        parsed=ParsedQuery(
            time_budget_minutes=req.time_budget_minutes,
            source="explicit",
        ),
        points=to_places(route, facts, keys),
        shape=measured.get("shape") or {},
        summary=RouteSummary(length_km=measured.get("length_km"), time_seconds=walk_s),
        costing=costing,
        budget=budget_info(
            measured,
            budget_minutes=req.time_budget_minutes,
            stops_dropped=int((measured.get("provenance") or {}).get("stops_dropped") or 0),
        ),
        explanation=explain_points(route, facts),
        status="ready",
        changes=None,
    )

"""Turning a run's outcome into the wire."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from agent.client import AgentOutcome
from agent.models import BriefStop, ReaderBrief, RefinementBrief, TripRequirements
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
    RouteChange,
    RouteChanges,
    RouteResponse,
    RouteSummary,
)
from core import constants
from core.errors import NoRoutePossibleError, UpstreamUnavailableError
from routing.cost import visit_time_minutes
from routing.models import Candidate, CategoryFacts
from routing.render import render
from routing.stops import candidate_of
from routing.valhalla.constants import costing_for

log = logging.getLogger(__name__)


def to_places(candidates: list[Candidate], facts: CategoryFacts) -> list[Place]:
    """Candidates → the response points (one mapping, used by every branch)."""
    return [
        Place(
            id=p.id,
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


def render_tour(
    route: list[Candidate],
    *,
    costing: str,
    origin: Any,
    round_trip: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Draw the tour, never failing the request over geometry."""
    try:
        shape, summary, status = render(
            route, costing=costing, origin=origin, round_trip=round_trip
        )
        if status != "usable":
            log.info("render returned status: %s", status)
        return shape, summary
    except UpstreamUnavailableError as exc:
        log.warning("render failed (%s) — answering without geometry", exc)
        return {}, {}


def route_response(
    outcome: AgentOutcome,
    *,
    repos: Any,
    facts: CategoryFacts,
    deadline: dict[str, Any] | None = None,
    previous: Sequence[BriefStop] | None = None,
) -> RouteResponse:
    """The one place a run's outcome becomes the response body."""
    contract = outcome.requirements
    measured = outcome.measured

    if outcome.refused:
        return _refusal_response(outcome, deadline=deadline)

    route = _ordered_candidates(repos, outcome)

    walk_s = float(measured.get("walk_minutes") or 0) * 60
    length_km = measured.get("length_km")
    costing = costing_for(outcome.requirements.costing)
    narrative = outcome.submission.narrative or None

    offers = alternatives_for(costing=costing, walk_s=walk_s, length_km=length_km)
    if offers and narrative:
        narrative = f"{narrative}\n\n{alternatives_sentence(offers, walk_s)}"

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
        budget=BudgetInfo(
            budget_minutes=contract.budget_minutes,
            walk_minutes=int(walk_s / 60) + 1,
            visit_minutes=int(measured.get("visit_minutes") or 0),
            total_minutes=int(measured.get("total_minutes") or 0),
            fits=bool(measured.get("fits_budget")),
            stops_dropped=int(measured.get("provenance", {}).get("stops_dropped") or 0),
        ),
        explanation=narrative,
        alternatives=offers or None,
        status=outcome.status,
        requirements=contract.public_requirements(),
        interpretation=interpretation(contract, outcome.status),
        costing=costing,
        changes=route_changes(previous or (), points),
        debug={
            "requirements_source": contract.source,
            "problems": outcome.problems,
            "verdicts": measured.get("verdicts"),
            "deadline": deadline,
        },
    )


DEFAULT_REFUSAL = (
    "Маршрут не построен: в запросе нет мест внутри обслуживаемого региона "
    "(Гродненская область). Уточните запрос."
)


def _refusal_response(
    outcome: AgentOutcome, *, deadline: dict[str, Any] | None = None
) -> RouteResponse:
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
            "deadline": deadline,
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
    region_bbox: tuple[float, float, float, float] | None = None
    if req.region_bbox and len(req.region_bbox) == constants.BBOX_COORDS:
        region_bbox = (
            float(req.region_bbox[0]),
            float(req.region_bbox[1]),
            float(req.region_bbox[2]),
            float(req.region_bbox[3]),
        )
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
        origin=origin,
        region_bbox=region_bbox,
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


def reroute_response(req: RerouteReq, *, repos: Any) -> RouteResponse:
    """Route a list of points the client already chose, and draw it."""
    facts = CategoryFacts.from_rows(repos.taxonomy.all())
    rows = repos.places.get_by_ids(req.point_ids)
    found = {row.id: row for row in rows}
    missing = [pid for pid in req.point_ids if pid not in found]
    if missing:
        raise NoRoutePossibleError(f"unknown point_ids: {sorted(missing)}")

    candidates = [candidate_of(found[pid], 0.0) for pid in req.point_ids]
    costing = costing_for(req.profile)
    shape, summary = render_tour(candidates, costing=costing, origin=None)
    walk_s = float((summary or {}).get("time", 0.0) or 0.0)
    return RouteResponse(
        parsed=ParsedQuery(source="explicit"),
        points=to_places(candidates, facts),
        shape=shape,
        summary=RouteSummary(length_km=(summary or {}).get("length"), time_seconds=walk_s),
        costing=costing,
        budget=None,
        explanation=explain_points(candidates, facts),
        changes=None,
    )

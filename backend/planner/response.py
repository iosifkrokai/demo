"""The response-shaping helpers: candidates → points, trace fragments, offers.

Turns a planned route into the interpretation block, verdicts and Place list.
"""

from __future__ import annotations

import logging
from typing import Any

from agent import interpret_cache
from core import constants
from core.errors import UpstreamUnavailable
from planner.models import (
    BudgetInfo,
    Candidate,
    Interpretation,
    LatLon,
    OverallStatus,
    ParsedQuery,
    Place,
    PlannedAlternative,
    RequirementSignal,
    RouteChanges,
    RouteResponse,
    RouteSummary,
)

from .cost import visit_time_minutes
from .geo import _TRACE_NAMES_MAX
from .render import render

log = logging.getLogger(__name__)


def build_response(
    *,
    intent,
    constraints,
    plan,
    changes: RouteChanges | None,
    shape: dict,
    walk_s: float,
    length_km: float | None,
    explanation: str,
    costing: str = "pedestrian",
    requirements=None,
    status: OverallStatus | None = None,
    deadline: dict | None = None,
) -> RouteResponse:
    """The one place a planned route becomes the response body.

    Earlier there were three: this, the catalogue branch, and a refusal built in
    the HTTP layer. They differed in their defaults, which is exactly the kind of
    drift a single builder prevents.
    """
    d = intent.decision
    offers = alternatives_for(costing=costing, walk_s=walk_s, length_km=length_km)
    if offers:
        explanation = f"{explanation}\n\n{alternatives_sentence(offers, walk_s)}"
    return RouteResponse(
        parsed=ParsedQuery(
            keywords=d.keywords_pos,
            categories=d.categories_pos,
            time_budget_minutes=d.time_budget_minutes,
            source=intent.source,
        ),
        points=_to_places(plan.route),
        shape=shape,
        summary=RouteSummary(length_km=length_km, time_seconds=walk_s),
        changes=changes,
        costing=costing,
        budget=BudgetInfo(
            budget_minutes=constraints.time_budget_minutes,
            walk_minutes=int(walk_s / 60) + 1,
            visit_minutes=plan.visit_seconds // 60,
            total_minutes=plan.total_seconds // 60,
            fits=plan.fits_budget,
            stops_dropped=plan.stops_dropped,
        ),
        explanation=explanation,
        alternatives=offers or None,
        status=status,
        requirements=(
            requirements.public_requirements() if requirements is not None else None
        ),
        interpretation=_interpretation(requirements, status),
        debug={
            "intent_source": intent.source,
            "intent_latency_ms": intent.latency_ms,
            "deadline": deadline,
            "cache": interpret_cache.stats(),
            "requirements_source": (
                getattr(requirements, "source", None) if requirements is not None else None
            ),
            "constraints": {
                "must_visit_ids": constraints.must_visit_ids,
                "area_anchor": constraints.area_anchor,
                "optional_categories": constraints.optional_categories,
                "forbidden_categories": constraints.forbidden_categories,
                "time_budget_minutes": constraints.time_budget_minutes,
            },
            "trace": plan.trace,
        },
    )


def _interpretation(
    requirements: Any | None, status: OverallStatus | None
) -> Interpretation | None:
    """What the system understood, in one compact block for the client.

    Codes and numbers only — the client localises ``code``/``reason`` itself.
    """
    if requirements is None:
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

    signals = [signal(r) for r in requirements.requirements]
    return Interpretation(
        source=requirements.source,
        locale=requirements.locale,
        status=status or "pending",
        adults=requirements.party.adults,
        children=requirements.party.children,
        children_ages=list(requirements.party.children_ages),
        mobility=list(requirements.party.mobility),
        budget_minutes=requirements.budget_minutes,
        areas=list(requirements.areas),
        transport=requirements.costing,
        result_mode=requirements.result_mode,
        round_trip=requirements.round_trip,
        requirements=signals,
        unmet=[s for s in signals if s.status != "satisfied"],
        unknowns=list(requirements.unknowns),
    )


def _verdicts(requirements: Any) -> list[dict[str, Any]]:
    """One line per requirement: what was asked, what the verifier decided, why.

    Reads back the verdict the verifier wrote onto the requirement objects.
    """
    return [
        {
            "asked": r.code or r.name or r.label or r.text,
            "kind": r.kind,
            "strength": r.strength,
            "status": r.status,
            "reason": r.reason,
            "place_ids": list(r.place_ids),
        }
        for r in requirements.requirements
    ]


def _to_places(candidates: list[Candidate]) -> list[Place]:
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
            visit_minutes=p.visit_minutes_db or visit_time_minutes(p.category),
        )
        for p in candidates
    ]


def _render_tour(
    route: list[Candidate],
    *,
    costing: str,
    origin: LatLon | None,
    round_trip: bool = False,
) -> tuple[dict, dict]:
    """Draw the tour, never failing the request over geometry.

    If nothing draws, answer with the stops and no line instead of a 500.
    """
    try:
        shape, summary, status = render(
            route, costing=costing, origin=origin, round_trip=round_trip
        )
        if status != "usable":
            log.info("render returned status: %s", status)
        return shape, summary
    except UpstreamUnavailable as exc:
        log.warning("render failed (%s) — answering without geometry", exc)
        return {}, {}


def _services_along_evidence(
    places: Any, requirements: Any, shape: Any
) -> Any:
    """Measure the services beside the line for the codes the requirements name.

    Returns None when nothing to measure; ``measured=False`` is a failed query.
    """
    codes = sorted(
        {
            r.code
            for r in getattr(requirements, "requirements", [])
            if r.kind in ("service", "interest") and r.code
        }
    )
    if not codes or not shape:
        return None
    from db.store.services import MAX_SERVICES

    from .verify import ServiceAlongEvidence

    try:
        answer = places.services_along(shape, categories=codes, limit=MAX_SERVICES * 2)
    except Exception:
        log.warning("services_along: measurement failed", exc_info=True)
        return ServiceAlongEvidence(measured=False, by_code={})
    out: dict[str, list[dict]] = {}
    for item in answer.get("items", []):
        out.setdefault(str(item.get("category")), []).append(item)
    return ServiceAlongEvidence(measured=True, by_code=out)


def alternatives_for(
    *, costing: str, walk_s: float, length_km: float | None
) -> list[PlannedAlternative]:
    """What to offer when the plan outgrows the profile the tourist chose.

    The far stops are NOT dropped; only costings we can genuinely route are offered.
    """
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


_MODE_WORDS = {
    "bicycle": "на велосипеде",
    "auto": "на машине или такси",
}


def alternatives_sentence(offers: list[PlannedAlternative], walk_s: float) -> str:
    """The tourist's version of the same news, in the request's language."""
    if not offers:
        return ""
    hours = walk_s / 3600.0
    span = f"{hours:.1f} ч" if hours >= 1 else f"{int(walk_s / 60)} мин"
    modes = ", ".join(_MODE_WORDS.get(o.costing, o.costing) for o in offers)
    return (
        f"Пешком это не прогулка: {span} в пути. "
        f"Варианты: {modes}; часть пути можно проехать на автобусе, маршрутке "
        f"или троллейбусе."
    )


def _gone(before: list[Any], after: list[Any]) -> list[str]:
    """The names a step removed, in the order they arrived.

    Capped by ``_TRACE_NAMES_MAX`` so a wide pool does not flood the trace.
    """
    kept = {c.id for c in after}
    return [c.name for c in before if c.id not in kept][:_TRACE_NAMES_MAX]


def _names(candidates: list[Any]) -> list[str]:
    """The first few names of a pool, for a step's Input panel.

    What a step *received*, capped at ``_TRACE_NAMES_MAX``.
    """
    return [c.name for c in candidates[:_TRACE_NAMES_MAX]]

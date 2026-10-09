"""The response-shaping helpers: candidates → points, trace fragments, offers.

Everything the pipeline turns a planned route into: the compact interpretation
block, the per-requirement verdicts for the trace, the response `Place` list,
the geometry drawing wrapper, the services-along measurement, and the
profile-outgrown alternatives.
"""

from __future__ import annotations

import logging
from typing import Any

from contracts.planner import (
    Candidate,
    Interpretation,
    LatLon,
    OverallStatus,
    Place,
    PlannedAlternative,
    RequirementSignal,
)
from core.errors import UpstreamUnavailable
from domain import constants

from .cost import visit_time_minutes
from .geo import _TRACE_NAMES_MAX
from .render import render

log = logging.getLogger(__name__)


def _interpretation(
    requirements: Any | None, status: OverallStatus | None
) -> Interpretation | None:
    """What the system understood, in one compact block for the client.

    Codes and numbers only — the client localises ``code``/``reason`` itself.
    ``unmet`` lists EVERY requirement that the verifier did not prove satisfied
    (unmet, uncertain or still pending), so a request whose mandatory stop could
    not be placed is reported explicitly instead of quietly returning a plan
    that ignores it.
    """
    if requirements is None:
        return None
    # Per-requirement provenance defaults to wherever the reading came from;
    # a requirement the user set with a visible control keeps "ui".
    origin = "agent" if requirements.source in ("llm", "mixed") else "fallback"

    def signal(r: Any) -> RequirementSignal:
        return RequirementSignal(
            kind=r.kind,
            strength=r.strength,
            code=r.code,
            name=r.name,
            origin="ui" if r.source == "ui" else origin,
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

    The verifier writes its verdict onto the requirement objects themselves
    (``status``/``reason``/``place_ids``); this reads that back in the shape a
    trace reader wants. Without it the `verify` step reports only the overall
    status, which is the one thing a reader cannot ask a question about — «почему
    туалет не выполнен» needs the per-requirement line.
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

    render() already falls back to per-leg geometry; if even that yields
    nothing we answer with the stops and no line, which the UI can explain,
    instead of a 500 for a tour that was planned fine.
    """
    try:
        shape, summary, status = render(
            route, costing=costing, origin=origin, round_trip=round_trip
        )
        # Log status for monitoring, but don't fail the request
        if status != "usable":
            log.info("render returned status: %s", status)
        return shape, summary
    except UpstreamUnavailable as exc:
        log.warning("render failed (%s) — answering without geometry", exc)
        return {}, {}


def _services_along_evidence(
    db: Any, requirements: Any, shape: Any
) -> Any:
    """Measure the services beside the line for the codes the requirements name.

    The verifier decides what a requirement means; it owns no database, so this
    is where the geometry is actually measured (``agent.services``). Returns

    * ``None`` — nothing to measure (no service/interest codes, or no usable
      line): the older semantics stay, so an absent café is still honestly
      ``unmet``;
    * ``ServiceAlongEvidence(measured=True, by_code=...)`` — measured. A code
      missing from the mapping means «рядом нет», which is evidence;
    * ``ServiceAlongEvidence(measured=False, ...)`` — the measurement itself
      failed. A broken query is not evidence that the café is absent, so the
      verifier reports ``uncertain`` rather than ``unmet``.
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
    from store import services as services_mod

    from .verify import ServiceAlongEvidence

    try:
        answer = services_mod.services_along(db, shape, categories=codes, limit=services_mod.MAX_SERVICES * 2)
    except Exception:  # measurement is best-effort; its failure is reported, not hidden
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

    Pedestrian is the default, and the honest answer to «все костёлы Гродненской
    области» measured 17 hours and 211 km of walking — a plan nobody can walk. The
    far stops are NOT dropped: the request really did ask for the whole region, and
    a silently trimmed dozen would lie about it. Instead the answer says the plan
    cannot be walked and names the ways to actually do it.

    Only costings we can genuinely route are offered, because the client submits
    them back as `profile` — a suggestion we cannot serve is its own broken
    promise. A taxi is an `auto` route; public transport is mentioned in the
    sentence rather than offered as a costing, because transit tiles are not
    loaded in this deployment and Valhalla would refuse the request.
    """
    if costing not in ("pedestrian", "bicycle"):
        # Already motorised: nothing in the answer is out of the profile's reach.
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


#: The tourist-facing name of each costing we offer. Machine identifiers have no
#: business in a sentence a person reads — the same rule the reason codes follow.
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

    A count on its own cannot answer «почему этой остановки нет в маршруте»: the
    trace used to say ``before=50 candidates=46`` and leave the reader to guess
    which four went. The names are the answer. They are capped by
    ``_TRACE_NAMES_MAX`` so that a wide regional pool does not turn one span into
    a page nobody reads — the count next to it stays exact.
    """
    kept = {c.id for c in after}
    return [c.name for c in before if c.id not in kept][:_TRACE_NAMES_MAX]


def _names(candidates: list[Any]) -> list[str]:
    """The first few names of a pool, for a step's Input panel.

    What a step *received*, next to the facts that say what it did with it. The
    trace used to show an input panel that was empty for every deterministic
    step, so a reader could see «50 → 45» without ever seeing the fifty.
    """
    return [c.name for c in candidates[:_TRACE_NAMES_MAX]]

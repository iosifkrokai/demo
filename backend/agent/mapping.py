"""AgentReading -> TripRequirements, including the merge with the UI fields."""

from __future__ import annotations

import logging

from agent.schema import AgentReading
from contracts.planner import GenerateReq
from domain import constants
from domain.requirements import PartyComposition, Requirement, TripRequirements
from domain.taxonomy import resolve_code

log = logging.getLogger(__name__)


def _verbatim(fragment: str | None, query: str) -> str | None:
    """Keep a provenance fragment only if it is really from the query.

    A fragment that does not occur in the request is dropped; the requirement survives.
    """
    if not isinstance(fragment, str):
        return None
    frag = " ".join(fragment.split())
    hay = " ".join(query.split()).lower()
    if not frag or frag.lower() not in hay:
        return None
    return frag


def ui_requirements(req: GenerateReq) -> list[Requirement]:
    """Requirements the tourist set with a visible control (source="ui")."""
    out: list[Requirement] = []
    for code in req.hard_services:
        out.append(Requirement(kind="service", strength="hard", code=code, label=code, source="ui"))
    for code in req.interests:
        out.append(
            Requirement(kind="interest", strength="soft", code=code, label=code, source="ui")
        )
    for code in req.avoid:
        out.append(Requirement(kind="avoid", strength="hard", code=code, label=code, source="ui"))
    return out


def ui_used(req: GenerateReq) -> bool:
    """True when an explicit control contributed anything to the request."""
    return bool(
        req.hard_services
        or req.interests
        or req.avoid
        or req.party_adults is not None
        or req.party_children is not None
        or bool(req.party_children_ages)
        or bool(req.mobility)
        or req.time_budget_minutes not in (None, 0)
    )


def _reading_requirements(
    reading: AgentReading, query: str, observed: set[int]
) -> tuple[list[Requirement], list[str]]:
    """A territory is not a stop: it is not made into a requirement, and it is not
    an area either.
    """
    from domain import areas as areas_mod

    out: list[Requirement] = []
    unsupported: list[str] = []
    for item in reading.requirements:
        code = None
        if item.kind != "must_visit":
            code = resolve_code(item.code or item.name or "")
            if code is None:
                ask = (item.code or item.name or item.text or item.kind).strip()
                unsupported.append(ask)
                continue
        elif not (item.name or "").strip():
            unsupported.append(item.kind)
            continue
        elif (
            isinstance(item.place_id, int) and item.place_id in observed
        ) is False and areas_mod.resolve_area((item.name or "").strip()):
            log.info(
                "interpretation: must_visit %r — территория (%s), не остановка",
                item.name, areas_mod.resolve_area((item.name or "").strip()),
            )
            continue

        place_id = None
        if (
            item.kind == "must_visit"
            and isinstance(item.place_id, int)
            and item.place_id in observed
        ):
            place_id = item.place_id
        out.append(
            Requirement(
                kind=item.kind,
                strength=item.strength,
                code=code,
                label=code or item.name,
                name=item.name if item.kind == "must_visit" else None,
                place_id=place_id,
                text=_verbatim(item.text, query),
                source="text",
                confidence=max(0.0, min(1.0, float(item.confidence))),
            )
        )
    return out, unsupported


def _party(req: GenerateReq, reading: AgentReading, query: str) -> PartyComposition:
    """Party composition: explicit values win, ages are never invented."""
    adults = req.party_adults if req.party_adults is not None else reading.adults
    children = req.party_children if req.party_children is not None else reading.children
    if req.party_children_ages:
        ages = list(req.party_children_ages)
    else:
        ages = [a for a in reading.children_ages if str(a) in query]
    mobility: list[str] = []
    for code in list(req.mobility) + list(reading.mobility):
        if isinstance(code, str) and code and code not in mobility:
            mobility.append(code)
    return PartyComposition(adults=adults, children=children, children_ages=ages, mobility=mobility)


def _unknowns(reading: AgentReading, unsupported: list[str], mobility: list[str]) -> list[str]:
    """What the system cannot represent or prove — named, never promised."""
    unknowns = list(reading.unknowns)
    for ask in unsupported:
        entry = f"unknown_category:{ask}"
        if entry not in unknowns:
            unknowns.append(entry)
    if "wheelchair" in mobility and "wheelchair_accessible" not in unknowns:
        unknowns.append("wheelchair_accessible")
    return unknowns


def _merge(
    query: str, req: GenerateReq, reading: AgentReading, observed: set[int]
) -> TripRequirements:
    """Build the contract: UI first (it wins), then the agent's reading."""
    ui_reqs = ui_requirements(req)
    requirements = list(ui_reqs)
    claimed = {(r.kind, r.code or r.name) for r in ui_reqs}

    agent_reqs, unsupported = _reading_requirements(reading, query, observed)
    for r in agent_reqs:
        key = (r.kind, r.code or r.name)
        if key in claimed:
            continue
        claimed.add(key)
        requirements.append(r)

    party = _party(req, reading, query)

    if req.time_budget_minutes is not None:
        budget = req.time_budget_minutes or None
    else:
        budget = reading.budget_minutes
    if budget is not None:
        budget = max(constants.MIN_BUDGET_MIN, min(int(budget), constants.MAX_BUDGET_MIN))

    areas: list[str] = []
    for slug in reading.areas:
        if isinstance(slug, str) and slug.strip() and slug.strip() not in areas:
            areas.append(slug.strip())

    return TripRequirements(
        locale=req.locale,
        raw_query=query,
        party=party,
        budget_minutes=budget,
        costing=req.profile,
        origin_lat=req.origin.lat if req.origin is not None else None,
        origin_lon=req.origin.lon if req.origin is not None else None,
        areas=areas,
        outside_coverage=_outside_names(reading.outside_coverage),
        result_mode=reading.result_mode or req.result_mode,
        round_trip=req.round_trip,
        requirements=requirements,
        unknowns=_unknowns(reading, unsupported, party.mobility),
        source="mixed" if ui_used(req) else "llm",
    )


def _outside_names(raw: list[str]) -> list[str]:
    """Names the model placed outside the region, de-duplicated and trimmed."""
    out: list[str] = []
    for name in raw:
        if not isinstance(name, str):
            continue
        cleaned = name.strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


# Used by agent.client outside this module.
__all__ = ["_merge", "_reading_requirements", "ui_requirements", "ui_used"]

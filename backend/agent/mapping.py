"""PlanSubmission -> TripRequirements, including the merge with the UI fields."""

from __future__ import annotations

import logging

from agent.models import PartyComposition, ReaderBrief, Requirement, TripRequirements
from agent.schema import PlanSubmission
from core import constants
from db.store.registry import Repositories

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


def ui_requirements(brief: ReaderBrief) -> list[Requirement]:
    """Requirements the tourist set with a visible control (source="ui")."""
    out: list[Requirement] = []
    for code in brief.hard_services:
        out.append(Requirement(kind="service", strength="hard", code=code, label=code, source="ui"))
    for code in brief.interests:
        out.append(
            Requirement(kind="interest", strength="soft", code=code, label=code, source="ui")
        )
    for code in brief.avoid:
        out.append(Requirement(kind="avoid", strength="hard", code=code, label=code, source="ui"))
    return out


def ui_used(brief: ReaderBrief) -> bool:
    """True when an explicit control contributed anything to the request."""
    return bool(
        brief.hard_services
        or brief.interests
        or brief.avoid
        or brief.party_adults is not None
        or brief.party_children is not None
        or bool(brief.party_children_ages)
        or bool(brief.mobility)
        or brief.time_budget_minutes not in (None, 0)
    )


def _reading_requirements(
    reading: PlanSubmission, query: str, observed: set[int], repos: Repositories | None
) -> tuple[list[Requirement], list[str]]:
    """A territory is not a stop: it is not made into a requirement, and it is not
    an area either.
    """

    out: list[Requirement] = []
    unsupported: list[str] = []
    for item in reading.requirements:
        code = None
        if item.kind != "must_visit":
            code = (
                repos.taxonomy.resolve_code(item.code or item.name or "")
                if repos is not None
                else None
            )
            if code is None:
                ask = (item.code or item.name or item.text or item.kind).strip()
                unsupported.append(ask)
                continue
        elif not (item.name or "").strip():
            unsupported.append(item.kind)
            continue
        elif not (isinstance(item.place_id, int) and item.place_id in observed):
            territory = (
                repos.areas.resolve((item.name or "").strip()) if repos is not None else None
            )
            if territory:
                log.info(
                    "interpretation: must_visit %r — территория (%s), не остановка",
                    item.name,
                    territory,
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


def _party(brief: ReaderBrief, reading: PlanSubmission, query: str) -> PartyComposition:
    """Party composition: explicit values win, ages are never invented."""
    adults = brief.party_adults if brief.party_adults is not None else reading.adults
    children = brief.party_children if brief.party_children is not None else reading.children
    if brief.party_children_ages:
        ages = list(brief.party_children_ages)
    else:
        ages = [a for a in reading.children_ages if str(a) in query]
    mobility: list[str] = []
    for code in list(brief.mobility) + list(reading.mobility):
        if code and code not in mobility:
            mobility.append(code)
    return PartyComposition(adults=adults, children=children, children_ages=ages, mobility=mobility)


def _unknowns(reading: PlanSubmission, unsupported: list[str], mobility: list[str]) -> list[str]:
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
    query: str,
    brief: ReaderBrief,
    reading: PlanSubmission,
    observed: set[int],
    repos: Repositories | None,
) -> TripRequirements:
    """Build the contract: UI first (it wins), then the agent's reading."""
    ui_reqs = ui_requirements(brief)
    requirements = list(ui_reqs)
    claimed = {(r.kind, r.code or r.name) for r in ui_reqs}

    agent_reqs, unsupported = _reading_requirements(reading, query, observed, repos)
    for r in agent_reqs:
        key = (r.kind, r.code or r.name)
        if key in claimed:
            continue
        claimed.add(key)
        requirements.append(r)

    party = _party(brief, reading, query)

    if brief.time_budget_minutes is not None:
        budget = brief.time_budget_minutes or None
    else:
        budget = reading.budget_minutes
    if budget is not None:
        budget = max(constants.MIN_BUDGET_MIN, min(int(budget), constants.MAX_BUDGET_MIN))

    areas: list[str] = []
    for slug in reading.areas:
        if slug.strip() and slug.strip() not in areas:
            areas.append(slug.strip())

    # The submission's outcome decides route vs catalogue; a submission that says
    # neither (or refuses) leaves the brief's own result_mode standing.
    mode = reading.outcome if reading.outcome in ("route", "catalogue") else brief.result_mode

    return TripRequirements(
        locale=brief.locale,
        raw_query=query,
        party=party,
        budget_minutes=budget,
        costing=brief.profile,
        origin_lat=brief.origin[0] if brief.origin is not None else None,
        origin_lon=brief.origin[1] if brief.origin is not None else None,
        areas=areas,
        outside_coverage=_outside_names(reading.outside_coverage),
        result_mode=mode,
        round_trip=brief.round_trip,
        requirements=requirements,
        unknowns=_unknowns(reading, unsupported, party.mobility),
        source="mixed" if ui_used(brief) else "llm",
    )


def _outside_names(raw: list[str]) -> list[str]:
    """Names the model placed outside the region, de-duplicated and trimmed."""
    out: list[str] = []
    for name in raw:
        cleaned = name.strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


# Used by agent.client outside this module.
__all__ = ["_merge", "_reading_requirements", "ui_requirements", "ui_used"]

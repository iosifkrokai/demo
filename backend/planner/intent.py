"""Step 1 — the TripRequirements entry point.

Reading a free-text query is the model's job (agent_interpret). This module holds
the contract around it: the request→requirements entry point, the merge that
keeps an explicit UI choice from being lost, and the requirements→IntentResult
bridge that ``resolve()`` consumes.
"""

from __future__ import annotations

import logging as _logging

from agent import client, interpret_cache
from agent.mapping import ui_requirements, ui_used
from agent.model import model_name
from agent.prompts import compose_instructions
from agent.prompts.notes import _ui_note
from contracts.planner import GenerateReq, IntentDecision, IntentResult
from core import constants
from core.errors import InterpretationUnavailable
from domain.requirements import Requirement, TripRequirements
from telemetry import trace

from .preprocess import WORD_RE

log = _logging.getLogger(__name__)


def intent_from_requirements(
    requirements: TripRequirements, query: str
) -> IntentResult:
    """Derive the ``IntentResult`` that ``resolve()`` consumes from a contract."""
    cats_pos: list[str] = []
    for code in (
        requirements.interest_codes()
        + requirements.soft_service_codes()
        + requirements.hard_service_codes()
    ):
        if code and code in constants.CATEGORIES and code not in cats_pos:
            cats_pos.append(code)
    cats_neg = [
        c for c in requirements.avoid_codes()
        if c in constants.CATEGORIES and c not in cats_pos
    ]

    decision = IntentDecision(
        intent_type="vague" if not WORD_RE.search(query) else "discovery",
        categories_pos=cats_pos,  # type: ignore[arg-type]
        categories_neg=cats_neg,  # type: ignore[arg-type]
        keywords_pos=[],
        keywords_neg=[],
        named_places=[r.name for r in requirements.of_kind("must_visit") if r.name],
        narrative=[],
        time_budget_minutes=requirements.budget_minutes,
        era_hint="any",
        party_type="solo",
        search_scope=_scope_from_areas(requirements.areas),  # type: ignore[arg-type]
    )
    return IntentResult(
        decision=decision,
        source="agent",
        confidence=0.0,
        latency_ms=0,
        raw_response=None,
    )


def _scope_from_areas(areas: list[str]) -> str:
    """town / district / region, read off the territories the contract named."""
    from domain import areas as areas_mod

    kinds = {
        entry["kind"]
        for entry in (areas_mod.area_lookup_by_slug(slug) for slug in areas)
        if entry
    }
    if "region" in kinds:
        return "region"
    if "district" in kinds:
        return "district"
    return "town"


def _interpret_cache_key(
    query: str, req: GenerateReq
) -> tuple[str | None, str]:
    """None when the agent cannot run at all; the prompt is hashed into the key, so
    editing the instructions invalidates every entry.
    """
    try:
        if not client.available():
            return None, ""
        instructions = compose_instructions(_ui_note(req))
        return (
            interpret_cache.interpret_key(query, req, instructions, model_name()),
            interpret_cache.prompt_hash(instructions),
        )
    except Exception as exc:
        log.warning("requirements: cache key unavailable (%s)", exc)
        return None, ""


def _agent_contract(
    query: str, req: GenerateReq, db, wall_clock_s: float | None = None
) -> TripRequirements | None:
    """The interpretation agent's contract, or None when it cannot be trusted."""
    try:
        return client.interpret_with_agent(query, req, db=db, wall_clock_s=wall_clock_s)
    except Exception as exc:
        log.warning("requirements: agent layer failed (%s)", exc)
        return None


def mark_out_of_coverage(contract: TripRequirements, names: list[str]) -> None:
    """Marks each `hard`; a refusal is never invented here — the verifier reports
    what the contract keeps.
    """
    from domain.requirements import REASON_MUST_VISIT_OUTSIDE

    by_name = {
        (r.name or "").strip().lower(): r
        for r in contract.requirements
        if r.kind == "must_visit"
    }
    for name in names:
        key = name.strip().lower()
        if not key:
            continue
        r = by_name.get(key)
        if r is None:
            r = Requirement(
                kind="must_visit", name=name, label=name, text=name, source="text"
            )
            contract.requirements.append(r)
            by_name[key] = r
        r.strength = "hard"
        r.reason = REASON_MUST_VISIT_OUTSIDE
        r.place_id = None


def _finalize_agent_contract(
    contract: TripRequirements, req: GenerateReq
) -> TripRequirements:
    """Top up an agent contract with the facts the request states explicitly.

    The model reads the text; the controls the tourist actually pressed must not
    be lost to a misreading, so they are merged in and win on conflict.
    """
    ui_reqs = ui_requirements(req)
    contract.requirements, _ = _merge_requirements(ui_reqs, contract.requirements)
    ui_positive = {
        r.code for r in ui_reqs if r.code and r.kind in ("interest", "service")
    }
    contract.requirements = [
        r for r in contract.requirements
        if not (r.kind == "avoid" and r.code in ui_positive)
    ]

    if req.party_children is not None:
        contract.party.children = req.party_children
    if req.party_adults is not None:
        contract.party.adults = req.party_adults
    if req.party_children_ages:
        contract.party.children_ages = list(req.party_children_ages)
    for code in req.mobility:
        if code and code not in contract.party.mobility:
            contract.party.mobility.append(code)
    if req.time_budget_minutes is not None:
        contract.budget_minutes = req.time_budget_minutes or None

    if (
        "wheelchair" in contract.party.mobility
        and "wheelchair_accessible" not in contract.unknowns
    ):
        contract.unknowns.append("wheelchair_accessible")
    if contract.budget_minutes is not None:
        contract.budget_minutes = max(
            constants.MIN_BUDGET_MIN,
            min(contract.budget_minutes, constants.MAX_BUDGET_MIN),
        )
    if contract.source == "llm" and ui_used(req):
        contract.source = "mixed"
    return contract


def _merge_requirements(
    ui_reqs: list[Requirement], text_reqs: list[Requirement]
) -> tuple[list[Requirement], set[tuple[str, str | None]]]:
    """UI requirements first (they win); a text reading cannot duplicate them."""
    merged = list(ui_reqs)
    claimed = {(r.kind, r.code or r.name) for r in ui_reqs}
    for r in text_reqs:
        key = (r.kind, r.code or r.name)
        if key in claimed:
            continue
        claimed.add(key)
        merged.append(r)
    return merged, claimed


def build_requirements(
    query: str, req: GenerateReq, *, db: object | None = None,
    wall_clock_s: float | None = None,
) -> TripRequirements:
    """Interpret one request into the frozen `TripRequirements` contract.

    With no reading there is nothing to plan from: the request is refused rather
    than answered with a guess at what the tourist meant.
    """
    cache_key, prompt_hash = _interpret_cache_key(query, req)
    if cache_key is not None:
        cached = interpret_cache.INTERPRET_CACHE.get(cache_key)
        if cached is not None:
            log.info("requirements: cached reading (no model call)")
            trace.record("interpret · model", "skipped", cached=True)
            return cached.model_copy(deep=True)

    contract = _agent_contract(query, req, db, wall_clock_s)
    if contract is None:
        raise InterpretationUnavailable(
            "the query could not be read: the interpretation model is unavailable"
        )

    log.info(
        "requirements: agent reading (source=%s, %d requirement(s))",
        contract.source, len(contract.requirements),
    )
    final = _finalize_agent_contract(contract, req)
    if cache_key is not None:
        interpret_cache.INTERPRET_CACHE.put(
            cache_key, final.model_copy(deep=True), prompt_hash
        )
    return final

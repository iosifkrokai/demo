"""Applying ONE refinement operation to the route the user already has.

The instruction is read, one typed operation is applied (add / remove / reorder),
and the result goes back through the same cost → optimize → validate tail a fresh
plan does — in "refine" mode, where nothing is re-searched. Base stops survive
unless explicitly removed, so a refinement never answers 422.

These were methods on `Pipeline`, using `self` for the repositories and for three
sibling helpers and nothing else. They are functions over a `Repositories` now,
which is what they always were.
"""

from __future__ import annotations

import logging
import time as _time

from core import constants
from db.store.registry import Repositories
from planner.models import Candidate, GenerateReq, RouteResponse
from telemetry import trace

from .dedupe import _norm_name
from .explain import explain as explain_route
from .geo import _TRACE_NAMES_MAX
from .intent import build_requirements, intent_from_requirements
from .plan_tail import run_plan_tail
from .refine import (
    _cap_for_valhalla,
    _context_changes,
    _nearby_convenience,
    interpret_refinement,
    is_excluded_category,
    reason_text,
    reorder_stops,
)
from .resolve import resolve
from .response import (
    _render_tour,
    _services_along_evidence,
    _verdicts,
    build_response,
)
from .retrieve import candidate_of
from .turn import Turn
from .verify import overall_status, verify

log = logging.getLogger(__name__)


def refinement_base(repos: Repositories, ctx) -> list[Candidate]:
    """The previous route's stops, as Candidate objects.

    A hand-placed stop (map click, no DB id) is matched to the nearest place.
    """
    base_ids = [p.id for p in ctx.base_points if p.id is not None]
    base_rows = repos.places.get_by_ids(base_ids) if base_ids else []

    manual = [p for p in ctx.base_points if p.id is None and (p.pinned or p.source == "user")]
    for point in manual:
        try:
            near_places = repos.places.nearby(point.lat, point.lon, radius_km=0.06, limit=1)
        except Exception as exc:
            log.warning("context: hand-placed stop lookup failed: %s", exc)
            continue
        for place in near_places:
            if place.id is not None and place.id not in {r.id for r in base_rows}:
                base_rows.append(place)
                log.info(
                    "context: hand-placed stop «%s» matched to «%s»",
                    point.name,
                    place.name,
                )

    seen: set[int] = set()
    base: list[Candidate] = []
    for row in base_rows:
        if row.id is None or row.id in seen:
            continue
        seen.add(row.id)
        base.append(candidate_of(row, 0.0))
    return base


def _removals(directive, base: list[Candidate], excluded: set[int]) -> set[int]:
    """Stop ids the instruction asks to remove, on top of excluded_ids.

    Applied for every operation: add-and-remove still drops the removals.
    """
    removed = set(excluded)

    for stop in base:
        if is_excluded_category(stop, directive.exclude_categories):
            removed.add(stop.id)

    for name in directive.remove_names:
        norm = _norm_name(name)
        if not norm:
            continue
        for stop in base:
            if norm in _norm_name(stop.name):
                removed.add(stop.id)
    return removed


def _additions(repos: Repositories, directive, base: list[Candidate]) -> list[Candidate]:
    """Stops the instruction asks to ADD, taken near the route.

    Convenience categories come from the stops' neighbourhood, not a global ranking.
    """
    found: dict[int, Candidate] = {}
    base_ids = {c.id for c in base}

    try:
        convenience = {c for c in directive.add_categories if c in constants.CONVENIENCE_CATEGORIES}
        if convenience:
            for c in _nearby_convenience(repos.places, base, convenience):
                found[c.id] = c

        sights = {c for c in directive.add_categories if c not in constants.CONVENIENCE_CATEGORIES}
        if sights:
            for c in _nearby_convenience(
                repos.places,
                base,
                sights,
                radius_m=constants.CONVENIENCE_RADIUS_M * 4,
                max_added=constants.CONVENIENCE_MAX_ADDED,
            ):
                found.setdefault(c.id, c)
    except Exception as exc:
        log.warning("refinement: nearby add lookup failed: %s", exc)

    for name in directive.add_names:
        try:
            rows = repos.places.name_match(name, limit=1)
        except Exception as exc:
            log.warning("refinement: named add lookup failed: %s", exc)
            continue
        for place, _similarity in rows:
            if place.id is None or place.id in base_ids or place.id in found:
                continue
            found[place.id] = candidate_of(place, 0.0)

    return [c for c in found.values() if c.id not in base_ids]


def generate_refinement(
    req: GenerateReq, base: list[Candidate], t0: float, *, repos: Repositories
) -> RouteResponse:
    """Apply ONE refinement operation to the route the user already has.

    The base stops are kept unless explicitly removed; never 422.
    """
    ctx = req.context
    assert ctx is not None
    instruction = (ctx.instruction or "").strip()
    excluded = set(ctx.excluded_ids or [])
    directive = interpret_refinement(instruction)

    removed = _removals(directive, base, excluded)
    kept = [c for c in base if c.id not in removed]

    additions: list[Candidate] = []
    if directive.operation == "reorder":
        route = reorder_stops(
            kept,
            by=directive.reorder_by or "visit_minutes",
            descending=directive.descending,
            origin=req.origin,
        )
        algorithm = f"refinement_reorder_{directive.reorder_by}"
    elif directive.operation == "add":
        additions = _additions(repos, directive, kept)
        have = {c.id for c in kept}
        route = list(kept) + [c for c in additions if c.id not in have]
        route = _cap_for_valhalla(route, kept)
        algorithm = "refinement_add"
    else:
        route = list(kept)
        algorithm = "refinement_keep"

    info: dict = {
        "algorithm": algorithm,
        "order": list(range(len(route))),
        "refinement_operation": directive.operation,
    }
    trace.record(
        "refinement · op",
        input={
            "instruction": instruction,
            "base": [c.name for c in base][:_TRACE_NAMES_MAX],
            "excluded_ids": sorted(excluded)[:10],
        },
        operation=directive.operation,
        supported=directive.supported,
        reason_code=directive.reason_code,
        removed=len(removed),
        added=len(additions),
        removed_names=[c.name for c in base if c.id in removed],
        added_names=[c.name for c in additions],
        stops=len(route),
    )

    requirements = build_requirements(instruction or req.query, req, repos=repos)
    intent = intent_from_requirements(requirements, instruction or req.query)
    trace.record(
        "interpret",
        input=instruction or req.query,
        source=intent.source,
        result_mode=requirements.result_mode,
    )
    constraints = resolve(
        intent,
        explicit_time_budget=req.time_budget_minutes,
        explicit_bbox=req.region_bbox,
        explicit_round_trip=req.round_trip,
        places=repos.places,
    )
    trace.record(
        "resolve",
        input={
            "budget_minutes": requirements.budget_minutes,
            "explicit_budget_minutes": req.time_budget_minutes,
            "explicit_bbox": bool(req.region_bbox),
        },
        time_budget_minutes=constraints.time_budget_minutes,
        must_visit_ids=len(constraints.must_visit_ids),
        optional_categories=len(constraints.optional_categories),
    )
    costing = req.profile or "pedestrian"

    turn = Turn(req=req, t0=t0, costing=costing, tail_mode="refine", plan_info=info)
    _candidates, _cost, route, _info, plan = run_plan_tail(turn, route, constraints)
    trace.record(
        "validate",
        input=[c.name for c in route],
        stops=len(plan.route),
        fits_budget=plan.trace.get("fits_budget"),
        walk_seconds=plan.trace.get("walk_seconds"),
        budget_seconds=plan.trace.get("budget_seconds"),
        max_leg_seconds=plan.trace.get("max_leg_seconds"),
    )
    shape, summary = _render_tour(
        route,
        costing=costing,
        origin=req.origin,
        round_trip=constraints.round_trip,
    )
    trace.record(
        "render",
        input=[c.name for c in route],
        length_km=summary.get("length") if summary else None,
    )
    verify(
        requirements,
        plan,
        shape,
        _services_along_evidence(repos.places, requirements, shape),
    )
    status = overall_status(requirements)
    trace.record(
        "verify",
        input=[r.code or r.text for r in requirements.requirements][:10],
        plan_status=status,
        output=_verdicts(requirements),
    )

    walk_s = float(summary.get("time", 0.0)) if summary else 0.0
    if walk_s == 0.0 and plan.walk_seconds > 0:
        walk_s = plan.walk_seconds
    length_km = summary.get("length") if summary else None

    if directive.operation == "unsupported":
        explanation = directive.detail or reason_text(directive.reason_code or "")
    else:
        explanation = explain_route(route, plan.trace, walk_s, costing)
    trace.record(
        "explain",
        input=[c.name for c in plan.route],
        output=explanation,
    )

    changes = _context_changes(base, route)

    ms = int((_time.perf_counter() - t0) * 1000)
    log.info(
        "pipeline.refine op=%s reason=%s ms=%d base=%d route=%d added=%d",
        directive.operation,
        directive.reason_code,
        ms,
        len(base),
        len(route),
        len(additions),
    )
    trace.record(
        "response",
        input={"plan_status": status, "stops": len(route)},
        plan_status=status,
        stops=len(route),
        ms=ms,
        route=[c.name for c in route],
    )

    resp = build_response(
        intent=intent,
        constraints=constraints,
        plan=plan,
        changes=changes,
        shape=shape,
        walk_s=walk_s,
        length_km=length_km,
        explanation=explanation,
        costing=costing,
        requirements=requirements,
        status=status,
    )
    resp.debug = dict(resp.debug or {})
    resp.debug["refinement"] = {
        "operation": directive.operation,
        "supported": directive.supported,
        "reason_code": directive.reason_code,
        "detail": directive.detail,
        "instruction": instruction or None,
        "revision": ctx.revision,
        "reorder_by": directive.reorder_by,
        "descending": directive.descending,
        "add_categories": list(directive.add_categories),
        "exclude_categories": list(directive.exclude_categories),
        "remove_names": list(directive.remove_names),
        "base_stops": len(base),
        "kept": changes.kept,
    }
    return resp

"""Pipeline orchestrator.

Wires the 10 steps together; one instance, reused across requests.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Any

from agent import interpret_cache
from contracts.planner import (
    BudgetInfo,
    Candidate,
    GenerateReq,
    IntentDecision,
    IntentResult,
    OverallStatus,
    ParsedQuery,
    ResolvedConstraints,
    RouteChanges,
    RouteResponse,
    RouteSummary,
)
from core import constants
from core.config import openrouter_api_key
from core.errors import (
    NoCandidatesFound,
    NoRoutePossible,
    UpstreamUnavailable,
)
from db.store.registry import Repositories
from planner.valhalla_client import ping as valhalla_ping
from telemetry import progress, trace

from .catalogue import catalogue_response
from .cost import (
    _build_cost,
    _is_service_code,
    _is_sight_stop,
    _refinement_cost,
    _synthetic_cost,
    compute_cost_matrix,
)
from .coverage import _outside_left_unresolved, refuse_out_of_coverage
from .dedupe import _drop_duplicates, _dupe_pairs, _norm_name
from .diversity import mmr_select
from .explain import explain as explain_route
from .geo import (
    _TRACE_NAMES_MAX,
    _distance_from_origin_m,
    _distance_m,
    _distance_pt_m,
    _geo_focus,
    _geo_focus_report,
    _geo_report,
    should_skip_geo_focus,
)
from .intent import build_requirements, intent_from_requirements
from .optimize import _order_after_prune, _prune_unroutable, _valhalla_order, optimize
from .preprocess import preprocess
from .refine import (
    _cap_for_valhalla,
    _context_changes,
    _drop_excluded,
    _nearby_convenience,
    _with_base_points,
    interpret_refinement,
    is_excluded_category,
    reason_text,
    reorder_stops,
)
from .render import render
from .resolve import resolve
from .response import (
    _MODE_WORDS,
    _gone,
    _interpretation,
    _names,
    _render_tour,
    _services_along_evidence,
    _to_places,
    _verdicts,
    alternatives_for,
    alternatives_sentence,
)
from .retrieve import apply_negative_filter, candidate_of, retrieve
from .validate import validate
from .verify import overall_status, verify

__all__ = [
    "_MODE_WORDS",
    "_TRACE_NAMES_MAX",
    "_build_cost",
    "_build_response",
    "_cap_for_valhalla",
    "_context_changes",
    "_distance_from_origin_m",
    "_distance_m",
    "_distance_pt_m",
    "_drop_duplicates",
    "_drop_excluded",
    "_dupe_pairs",
    "_geo_focus",
    "_geo_focus_report",
    "_geo_report",
    "_gone",
    "_interpretation",
    "_is_service_code",
    "_is_sight_stop",
    "_names",
    "_nearby_convenience",
    "_norm_name",
    "_outside_left_unresolved",
    "_prune_unroutable",
    "_refinement_cost",
    "_render_tour",
    "_services_along_evidence",
    "_synthetic_cost",
    "_to_places",
    "_valhalla_order",
    "_verdicts",
    "_with_base_points",
    "alternatives_for",
    "alternatives_sentence",
    "catalogue_response",
    "refuse_out_of_coverage",
    "should_skip_geo_focus",
]

log = logging.getLogger(__name__)


def _embed_query(text: str) -> list[float]:
    """Embed a search query with the LOCAL model (ml.embeddings).

    Returns [] only when the model cannot be loaded — keyword-only fallback.
    """
    from ml import embeddings

    cache_key = interpret_cache.embed_key([text], embeddings.MODEL_NAME)
    cached = interpret_cache.EMBED_CACHE.get(cache_key)
    if cached:
        return cached[0]

    try:
        vec = embeddings.embed_query(text)
    except Exception as exc:
        log.warning("embed: local model unavailable (%s) — keyword-only retrieval", exc)
        return []
    if not vec:
        log.warning("embed: local model returned no vector — keyword-only retrieval")
        return []
    interpret_cache.EMBED_CACHE.put(cache_key, [vec])
    return vec


@dataclass
class _Turn:
    """The per-request state generate() threads through its steps.

    Holds the request, its start time, decisions and pool state — never `self`.
    """

    req: GenerateReq
    t0: float
    region_scope: bool = False
    catalogue: bool = False
    refuse: list[str] | None = None
    qvec: list[float] = field(default_factory=list)
    near: tuple[float, float] | None = None
    excluded: set[int] = field(default_factory=set)
    costing: str = "pedestrian"
    round_trip: bool = False
    all_candidates: list[Candidate] = field(default_factory=list)
    plan_info: dict[str, Any] | None = None
    shape: dict = field(default_factory=dict)
    walk_s: float = 0.0
    length_km: float | None = None
    base_candidates: list[Candidate] = field(default_factory=list)
    deadline_trim: int | None = None
    deadline_order_skipped: bool = False
    deadline_geometry_skipped: bool = False
    tail_mode: str = "generate"


class Pipeline:
    """Stateless planner. One instance, reused across requests."""

    def __init__(self, repos: Repositories):
        self.repos = repos

    @staticmethod
    def _left(t0: float) -> float:
        """Seconds left of this request's end-to-end deadline (may be < 0)."""
        return constants.REQUEST_DEADLINE_S - (_time.perf_counter() - t0)

    def generate(self, req: GenerateReq) -> RouteResponse:
        turn = _Turn(req=req, t0=_time.perf_counter())
        trace.record(
            "query",
            input=req.query,
            query=req.query,
            profile=req.profile,
            time_budget_minutes=req.time_budget_minutes,
            origin=bool(req.origin),
        )

        if req.context and req.context.base_points:
            base = self._refinement_base(req.context)
            if base:
                trace.record(
                    "refinement",
                    input=[p.name for p in req.context.base_points][:_TRACE_NAMES_MAX],
                    base_points=len(req.context.base_points),
                )
                return self._generate_refinement(req, base, turn.t0)

        pre = self._step_preprocess(turn)
        requirements, intent = self._step_interpret(turn, pre)
        constraints = self._step_resolve(turn, requirements, intent)
        if turn.refuse:
            return self._refuse_out_of_coverage(
                req, requirements, intent, constraints, turn.refuse, turn.t0
            )

        candidates = self._step_retrieve(turn, constraints)
        candidates = self._step_dedupe(turn, candidates)

        if turn.catalogue:
            trace.record(
                "catalogue",
                input=_names(candidates),
                candidates=len(candidates),
                names=_names(candidates),
            )
            return self._catalogue_response(
                req, requirements, intent, constraints, candidates, turn.t0
            )

        candidates = self._step_geo_focus(turn, candidates, constraints)
        candidates = self._step_diversify(turn, candidates, constraints)
        plan = self._step_plan(turn, candidates, constraints)

        shape = self._step_render(turn, plan)
        explanation = self._step_explain(turn, plan, shape)
        status = self._step_verify(turn, plan, requirements)

        ms = int((_time.perf_counter() - turn.t0) * 1000)
        log.info(
            "pipeline.ok query_len=%d ms=%d n_stops=%d walk_s=%.0f budget_min=%s "
            "source=%s status=%s",
            len(req.query), ms, len(plan.route), turn.walk_s,
            constraints.time_budget_minutes, intent.source, status,
        )

        changes = (
            _context_changes(turn.base_candidates, plan.route)
            if turn.base_candidates
            else None
        )

        trace.record(
            "response",
            input={"plan_status": status, "stops": len(plan.route)},
            plan_status=status,
            stops=len(plan.route),
            ms=int((_time.perf_counter() - turn.t0) * 1000),
            route=[p.name for p in plan.route],
        )

        return self._build_response(
            intent=intent,
            changes=changes,
            constraints=constraints,
            plan=plan,
            shape=turn.shape,
            walk_s=turn.walk_s,
            length_km=turn.length_km,
            explanation=explanation,
            costing=turn.costing,
            requirements=requirements,
            status=status,
            deadline={
                "budget_s": constants.REQUEST_DEADLINE_S,
                "used_s": round(_time.perf_counter() - turn.t0, 3),
                "pool_trimmed_to": turn.deadline_trim,
                "valhalla_order_skipped": turn.deadline_order_skipped,
                "geometry_skipped": turn.deadline_geometry_skipped,
            },
        )

    def _step_preprocess(self, turn: _Turn):
        pre = preprocess(turn.req.query)
        log.info(
            "preprocess: language=%s fingerprint=%s significant_words=%d",
            pre.language,
            pre.fingerprint,
            pre.n_significant_words,
        )
        trace.record(
            "preprocess",
            input=turn.req.query,
            language=pre.language,
            n_significant_words=pre.n_significant_words,
            is_short=pre.is_short,
            is_specific=pre.is_specific,
            fingerprint=pre.fingerprint,
        )
        return pre

    def _step_interpret(self, turn: _Turn, pre):
        progress.note(progress.STAGE_INTERPRETING)
        requirements = build_requirements(
            turn.req.query, turn.req, repos=self.repos, wall_clock_s=self._left(turn.t0)
        )
        intent = intent_from_requirements(requirements, turn.req.query)

        turn.region_scope = intent.decision.search_scope == "region"
        turn.catalogue = requirements.result_mode == "catalogue"
        trace.record(
            "interpret",
            input=turn.req.query,
            source=intent.source,
            result_mode=requirements.result_mode,
            region_scope=turn.region_scope,
        )
        return requirements, intent

    def _step_resolve(self, turn: _Turn, requirements, intent):
        constraints = resolve(
            intent,
            explicit_time_budget=turn.req.time_budget_minutes,
            explicit_bbox=turn.req.region_bbox,
            explicit_round_trip=turn.req.round_trip,
            outside=requirements.outside_coverage,
            places=self.repos.places,
        )
        trace.record(
            "resolve",
            input={
                "budget_minutes": requirements.budget_minutes,
                "outside_coverage": list(requirements.outside_coverage),
                "explicit_budget_minutes": turn.req.time_budget_minutes,
                "explicit_bbox": bool(turn.req.region_bbox),
            },
            time_budget_minutes=constraints.time_budget_minutes,
            must_visit_ids=len(constraints.must_visit_ids),
            optional_categories=len(constraints.optional_categories),
            must_visit=constraints.resolved_names[:_TRACE_NAMES_MAX],
            optional_names=constraints.optional_categories[:_TRACE_NAMES_MAX],
            forbidden_names=constraints.forbidden_categories[:_TRACE_NAMES_MAX],
            forbidden_keywords=constraints.forbidden_keywords[:_TRACE_NAMES_MAX],
            must_visit_keywords=constraints.must_visit_keywords[:_TRACE_NAMES_MAX],
            query_keywords=constraints.query_keywords[:_TRACE_NAMES_MAX],
            area_anchor=constraints.area_anchor,
            bbox=constraints.bbox,
            round_trip=constraints.round_trip,
            era_hint=constraints.era_hint,
            party_type=constraints.party_type,
            intent_type=constraints.intent_type,
        )
        turn.round_trip = constraints.round_trip

        outside_left = _outside_left_unresolved(requirements, constraints)
        trace.record(
            "coverage",
            input=[r.code or r.text for r in requirements.requirements][:5],
            outside=len(outside_left),
        )
        if outside_left:
            trace.record("refuse", input=turn.req.query, names=outside_left)
            turn.refuse = outside_left
        return constraints

    def _step_retrieve(
        self, turn: _Turn, constraints: ResolvedConstraints
    ) -> list[Candidate]:
        qvec = _embed_query(turn.req.query)
        turn.qvec = qvec
        trace.record("embed", input=turn.req.query, embedded=bool(qvec))

        geo_anchor = constraints.area_anchor or (
            constraints.must_visit_ids[0] if constraints.must_visit_ids else None
        )
        near: tuple[float, float] | None = None
        if turn.req.origin is not None:
            near = (turn.req.origin.lat, turn.req.origin.lon)
        elif geo_anchor is not None and not turn.region_scope:
            anchor_rows = self.repos.places.get_by_ids([geo_anchor])
            if anchor_rows:
                anchor = anchor_rows[0]
                near = (float(anchor.lat), float(anchor.lon))
        turn.near = near

        progress.note(progress.STAGE_SEARCHING)
        candidates = retrieve(
            constraints,
            qvec,
            self.repos.places,
            query_text=turn.req.query,
            near=near,
        )
        trace.record(
            "retrieve",
            input={"query": turn.req.query, "embedded": bool(qvec), "near": near},
            candidates=len(candidates),
            near=bool(near),
            ranked=[
                {
                    "name": c.name,
                    "relevance": round(c.relevance, 3),
                    "rrf": round(c.rrf_score, 3),
                }
                for c in candidates[:_TRACE_NAMES_MAX]
            ],
        )
        if not candidates:
            raise NoCandidatesFound(
                "no candidates matched the query — попробуйте другую формулировку"
            )

        return candidates

    def _step_dedupe(
        self, turn: _Turn, candidates: list[Candidate]
    ) -> list[Candidate]:
        before_dupes = candidates
        candidates = _drop_duplicates(candidates, constants.DUPLICATE_RADIUS_M)
        duplicates = _gone(before_dupes, candidates)

        excluded = set(turn.req.context.excluded_ids) if turn.req.context else set()
        turn.excluded = excluded
        user_removed: list[str] = []
        if excluded:
            before = len(candidates)
            before_excluded = candidates
            candidates = _drop_excluded(candidates, excluded)
            user_removed = _gone(before_excluded, candidates)
            log.info("context: dropped %d excluded stop(s)", before - len(candidates))
        trace.record(
            "dedupe",
            input=_names(before_dupes),
            before=len(before_dupes),
            candidates=len(candidates),
            excluded=len(excluded),
            duplicates=duplicates,
            merged=_dupe_pairs(before_dupes, candidates, constants.DUPLICATE_RADIUS_M),
            user_removed=user_removed,
        )
        return candidates

    def _step_geo_focus(
        self,
        turn: _Turn,
        candidates: list[Candidate],
        constraints: ResolvedConstraints,
    ) -> list[Candidate]:
        pool = len(candidates)
        before_geo = candidates
        skip_geo = should_skip_geo_focus(
            region_scope=turn.region_scope, origin=turn.req.origin
        )
        geo_report: dict[str, Any] = {"anchor": None, "radius_km": None, "dropped": []}
        if skip_geo:
            log.info("geo_focus skipped: region scope, no tourist position")
        else:
            geo_anchor = constraints.area_anchor or (
                constraints.must_visit_ids[0] if constraints.must_visit_ids else None
            )
            candidates, geo_report = _geo_focus_report(
                candidates, origin=turn.req.origin, anchor_id=geo_anchor
            )
        if len(candidates) < 2:
            raise NoCandidatesFound(
                "все кандидаты слишком далеко друг от друга — уточните город или район"
            )
        trace.record(
            "geo",
            "skipped" if skip_geo else "ok",
            input=_names(before_geo),
            candidates=len(candidates),
            before=pool,
            dropped=_gone(before_geo, candidates),
            anchor=geo_report["anchor"],
            radius_km=geo_report["radius_km"],
            outside=geo_report["dropped"],
            region_scope=turn.region_scope,
        )
        return candidates

    def _step_diversify(
        self,
        turn: _Turn,
        candidates: list[Candidate],
        constraints: ResolvedConstraints,
    ) -> list[Candidate]:
        pool = len(candidates)
        before_mmr = candidates
        if constraints.time_budget_minutes:
            candidates = mmr_select(
                candidates,
                n=min(constants.MMR_POOL_SIZE, len(candidates)),
                places=self.repos.places,
                constraints=constraints,
            )
        else:
            log.info("no time budget: skipping diversity trim, %d candidates", len(candidates))
        trace.record(
            "diversity",
            "ok" if constraints.time_budget_minutes else "skipped",
            input=_names(before_mmr),
            candidates=len(candidates),
            before=pool,
            mmr_ran=bool(constraints.time_budget_minutes),
            trimmed=len(candidates) < pool,
            dropped=_gone(before_mmr, candidates),
        )
        if len(candidates) < 2:
            raise NoCandidatesFound(
                f"only {len(candidates)} candidate(s) survived diversity filter"
            )
        return candidates

    def _step_plan(
        self,
        turn: _Turn,
        candidates: list[Candidate],
        constraints: ResolvedConstraints,
    ):
        turn.base_candidates = []

        turn.costing = turn.req.profile or ("auto" if turn.region_scope else "pedestrian")

        left = self._left(turn.t0)
        if left < constants.COST_MATRIX_MIN_LEFT_S and len(candidates) > constants.POOL_TRIM_SIZE:
            log.info(
                "deadline: %.1fs left — trimming %d candidates to %d before the cost matrix",
                left, len(candidates), constants.POOL_TRIM_SIZE,
            )
            candidates = sorted(candidates, key=lambda c: c.relevance, reverse=True)
            candidates = candidates[: constants.POOL_TRIM_SIZE]
            turn.deadline_trim = constants.POOL_TRIM_SIZE

        progress.note(progress.STAGE_SELECTING)
        all_candidates = list(candidates)
        turn.all_candidates = all_candidates
        sights = [c for c in candidates if _is_sight_stop(c)]
        widened: list[Any] = []

        if len(sights) < 3 and not turn.region_scope:
            sight_categories = [
                c for c in constraints.optional_categories if not _is_service_code(c)
            ]
            relaxed = constraints.model_copy(
                update={
                    "query_keywords": [],
                    "must_visit_keywords": [],
                    "optional_categories": sight_categories,
                }
            )
            wider = retrieve(relaxed, turn.qvec, self.repos.places, query_text="", near=turn.near)
            if relaxed.forbidden_categories or relaxed.forbidden_keywords:
                wider = apply_negative_filter(wider, relaxed)
            wider = _geo_focus(
                wider,
                origin=turn.req.origin,
                anchor_id=constraints.area_anchor
                or (constraints.must_visit_ids[0] if constraints.must_visit_ids else None),
            )
            have = {c.id for c in candidates}
            added = [c for c in wider if c.id not in have]
            if added:
                log.info(
                    "pool: %d sight(s) of %d — widening the search by the words "
                    "dropped %d more", len(sights), len(candidates), len(added),
                )
                candidates = _drop_excluded(candidates + added, turn.excluded)
                sights = [c for c in candidates if _is_sight_stop(c)]
                widened = added

        before_narrowing = candidates
        if 2 <= len(sights) < len(candidates):
            log.info(
                "pool: %d sight(s) of %d candidate(s) — services stay on the line",
                len(sights), len(candidates),
            )
            candidates = sights
        elif len(sights) < 2:
            log.info("pool: %d sight(s) — the services are the destination here", len(sights))

        trace.record(
            "pool",
            input=_names(before_narrowing),
            before=len(before_narrowing),
            candidates=len(candidates),
            widened=[c.name for c in widened[:_TRACE_NAMES_MAX]],
            services_off=_gone(before_narrowing, candidates),
        )

        _candidates, _cost, _route, _info, plan = self._plan_tail(
            turn, candidates, constraints
        )
        return plan

    def _plan_tail(
        self,
        turn: _Turn,
        candidates: list[Candidate],
        constraints: ResolvedConstraints,
    ):
        """cost → optimize → validate, shared by the three planning entry points.

        How the pool is costed, and whether an order is searched, is ``turn.tail_mode``.
        """
        costing = turn.costing

        if turn.tail_mode == "refine":
            route, cost = _refinement_cost(candidates, constraints, costing)
            info = turn.plan_info or {}
            plan = validate(route, cost, constraints, info)
            return candidates, cost, route, info, plan

        if turn.tail_mode == "reroute":
            cost = compute_cost_matrix(candidates, constraints, costing=costing)
            if cost.indices != list(range(len(candidates))):
                candidates = [candidates[i] for i in cost.indices]
            route, info = optimize(candidates, cost, constraints, costing=costing)
            plan = validate(route, cost, constraints, info)
            return candidates, cost, route, info, plan

        progress.note(progress.STAGE_MEASURING_LEGS)
        before_cost = candidates
        candidates, cost = _build_cost(candidates, constraints, costing)
        trace.record(
            "cost",
            input=_names(before_cost),
            before=len(before_cost),
            candidates=len(candidates),
            costing=costing,
            unreachable=_gone(before_cost, candidates),
        )

        progress.note(progress.STAGE_ORDERING)
        route, info = optimize(candidates, cost, constraints, costing=costing)
        by_id = {c.id: c.name for c in candidates}
        trace.record(
            "optimize",
            input={"candidates": _names(candidates), "costing": costing},
            stops=len(route),
            costing=costing,
            route=[c.name for c in route],
            algorithm=info.get("algorithm"),
            iterations=info.get("iterations"),
            must_missing=[
                by_id.get(i, str(i))
                for i in info.get("missing_must_visit_ids") or []
            ][:_TRACE_NAMES_MAX],
        )
        retry: dict[str, Any] | None = None
        if len(route) < 2 and len(candidates) > 1:
            sights = [c for c in candidates if _is_sight_stop(c)]
            if 2 <= len(sights) < len(candidates):
                log.info(
                    "optimize collapsed to %d stop(s) — retrying on %d sight stop(s)",
                    len(route), len(sights),
                )
                retry_candidates, retry_cost = _build_cost(sights, constraints, costing)
                retry_route, retry_info = optimize(
                    retry_candidates, retry_cost, constraints, costing=costing
                )
                if len(retry_route) >= 2:
                    candidates, cost, route, info = (
                        retry_candidates, retry_cost, retry_route, retry_info,
                    )
                    retry = {"why": "walkable", "pool": _names(sights)}
        if len(route) < 2 and len(turn.all_candidates) > len(candidates):
            log.info("optimize: sights alone give %d stop(s) — retrying with services", len(route))
            retry_candidates, retry_cost = _build_cost(turn.all_candidates, constraints, costing)
            retry_route, retry_info = optimize(
                retry_candidates, retry_cost, constraints, costing=costing
            )
            if len(retry_route) >= 2:
                candidates, cost, route, info = (
                    retry_candidates, retry_cost, retry_route, retry_info,
                )
                retry = {"why": "services_back", "pool": _names(turn.all_candidates)}
        if retry is not None:
            trace.record(
                "optimize · retry",
                input=retry["pool"],
                why=retry["why"],
                stops=len(route),
                algorithm=info.get("algorithm"),
            )
        if len(route) < 2:
            raise NoRoutePossible("optimizer could not produce a route with ≥ 2 stops")

        matrix_order = [c.name for c in route]
        if self._left(turn.t0) >= constants.VALHALLA_ORDER_MIN_LEFT_S:
            route, info = _valhalla_order(
                route,
                info,
                costing=costing,
                timeout=constants.VALHALLA_ORDER_TIMEOUT_S,
                retries=constants.VALHALLA_ORDER_RETRIES,
            )
        else:
            log.info(
                "deadline: %.1fs left — skipping Valhalla re-ordering",
                self._left(turn.t0),
            )
            turn.deadline_order_skipped = True
        trace.record(
            "order",
            "skipped" if turn.deadline_order_skipped else "ok",
            input=matrix_order,
            skipped=turn.deadline_order_skipped,
            before=matrix_order,
            route=[c.name for c in route],
        )

        planned = len(route)
        route, prune_report = _prune_unroutable(
            route, candidates, cost, constraints.must_visit_ids
        )
        info = _order_after_prune(info, route, candidates)
        trace.record(
            "prune",
            input=[c.name for c in route],
            stops=len(route),
            before=planned,
            dropped=planned - len(route),
            unroutable=[c.name for c in prune_report],
        )

        plan = validate(route, cost, constraints, info, prune_report=prune_report)
        trace.record(
            "validate",
            input=[c.name for c in route],
            stops=len(plan.route),
            fits_budget=plan.trace.get("fits_budget"),
            stops_dropped=plan.stops_dropped,
            dropped=(info.get("stops_dropped_names") or [])[:_TRACE_NAMES_MAX],
            walk_seconds=plan.trace.get("walk_seconds"),
            visit_seconds=plan.trace.get("visit_seconds"),
            total_seconds=plan.trace.get("total_seconds"),
            budget_seconds=plan.trace.get("budget_seconds"),
            max_leg_seconds=plan.trace.get("max_leg_seconds"),
            budget_exceeded=plan.trace.get("budget_exceeded"),
            stop_categories=plan.trace.get("categories"),
            diversity=plan.trace.get("diversity"),
        )
        return candidates, cost, route, info, plan

    def _step_render(self, turn: _Turn, plan) -> dict:
        progress.note(progress.STAGE_DRAWING)
        if self._left(turn.t0) >= constants.RENDER_MIN_LEFT_S:
            shape, summary = _render_tour(
                plan.route,
                costing=turn.costing,
                origin=turn.req.origin,
                round_trip=turn.round_trip,
            )
        else:
            log.info("deadline: %.1fs left — skipping geometry", self._left(turn.t0))
            shape, summary = {}, {}
            turn.deadline_geometry_skipped = True
        trace.record(
            "render",
            "skipped" if turn.deadline_geometry_skipped else "ok",
            input=[c.name for c in plan.route],
            skipped=turn.deadline_geometry_skipped,
            length_km=summary.get("length") if summary else None,
        )

        walk_s = float(summary.get("time", 0.0)) if summary else 0.0
        length_km = summary.get("length") if summary else None

        if walk_s == 0.0 and plan.walk_seconds > 0:
            walk_s = plan.walk_seconds

        turn.shape = shape
        turn.walk_s = walk_s
        turn.length_km = length_km
        return shape

    def _step_explain(self, turn: _Turn, plan, shape: dict) -> str:
        explanation = explain_route(plan.route, plan.trace, turn.walk_s, turn.costing)
        trace.record(
            "explain",
            input=[c.name for c in plan.route],
            output=explanation,
        )
        return explanation

    def _step_verify(self, turn: _Turn, plan, requirements) -> OverallStatus:
        progress.note(progress.STAGE_CHECKING)
        verify(
            requirements,
            plan,
            turn.shape,
            _services_along_evidence(self.repos.places, requirements, turn.shape),
        )
        status = overall_status(requirements)
        progress.note(progress.STAGE_DONE)
        trace.record(
            "verify",
            input=[r.code or r.text for r in requirements.requirements][:10],
            plan_status=status,
            output=_verdicts(requirements),
        )
        return status

    def _refuse_out_of_coverage(
        self,
        req: GenerateReq,
        requirements: Any,
        intent: Any,
        constraints: ResolvedConstraints,
        names: list[str],
        t0: float,
    ) -> RouteResponse:
        """Answer "not here" instead of planning a route somewhere else."""
        return refuse_out_of_coverage(
            self, req, requirements, intent, constraints, names, t0
        )

    def _catalogue_response(
        self,
        req: GenerateReq,
        requirements: Any,
        intent: Any,
        constraints: ResolvedConstraints,
        candidates: list[Candidate],
        t0: float,
    ) -> RouteResponse:
        """Answer with the matching places, grouped by town, and no route."""
        return catalogue_response(
            req, requirements, intent, constraints, candidates, t0
        )

    def reroute(self, point_ids: list[int], profile: str | None = None) -> RouteResponse:
        """Re-route a chosen list of place IDs."""
        rows = self.repos.places.get_by_ids(point_ids)
        if len(rows) != len(point_ids):
            missing = set(point_ids) - {r.id for r in rows}
            raise NoCandidatesFound(f"unknown point_ids: {sorted(missing)}")

        candidates = [candidate_of(r, 1.0) for r in rows]
        intent = IntentResult(
            decision=IntentDecision(intent_type="specific"), source="agent"
        )
        constraints = resolve(
            intent,
            explicit_time_budget=constants.MAX_BUDGET_MIN,
            explicit_bbox=None,
            places=self.repos.places,
        )
        constraints.must_visit_ids = list(point_ids)

        turn = _Turn(
            req=GenerateReq(query="точки пользователя"),
            t0=_time.perf_counter(),
            costing=profile or "pedestrian",
            tail_mode="reroute",
        )
        _candidates, _cost, _route, _info, plan = self._plan_tail(
            turn, candidates, constraints
        )

        try:
            shape, summary, _status = render(plan.route, costing=profile or "pedestrian")
        except UpstreamUnavailable:
            shape, summary = {}, {}
        walk_s = float((summary or {}).get("time", 0.0)) or plan.walk_seconds
        length_km = (summary or {}).get("length")

        return RouteResponse(
            parsed=ParsedQuery(source="explicit"),
            points=_to_places(plan.route),
            changes=None,
            shape=shape,
            summary=RouteSummary(length_km=length_km, time_seconds=walk_s),
            costing=profile or "pedestrian",
            budget=None,
            explanation=explain_route(plan.route, plan.trace, walk_s),
        )

    def explain_route(self, point_ids: list[int]) -> str:
        """Natural-language Russian description of a list of points."""
        rows = self.repos.places.get_by_ids(point_ids)
        if len(rows) != len(point_ids):
            missing = set(point_ids) - {r.id for r in rows}
            raise NoCandidatesFound(f"unknown point_ids: {sorted(missing)}")

        cands = [candidate_of(r, 1.0) for r in rows]
        trace = {"algorithm": "direct", "diversity": 1.0, "fits_budget": True}
        return explain_route(cands, trace, walk_seconds=0.0)

    def health(self) -> dict:
        """Cheap liveness/readiness snapshot."""
        db_ok = self.repos.places.ping()

        try:
            valhalla_ok = valhalla_ping()
        except Exception:
            valhalla_ok = False

        from ml import embeddings

        embedder_ok = embeddings.is_available()
        llm_ok = bool(openrouter_api_key())

        return {
            # A missing reader is not "ok": nothing can be planned without one.
            "status": "ok" if (db_ok and valhalla_ok and embedder_ok and llm_ok) else "degraded",
            "embedder": embedder_ok,
            "llm": llm_ok,
            "db": db_ok,
            "valhalla": valhalla_ok,
        }

    def _refinement_base(self, ctx) -> list[Candidate]:
        """The previous route's stops, as Candidate objects.

        A hand-placed stop (map click, no DB id) is matched to the nearest place.
        """
        base_ids = [p.id for p in ctx.base_points if p.id is not None]
        base_rows = self.repos.places.get_by_ids(base_ids) if base_ids else []

        manual = [
            p
            for p in ctx.base_points
            if p.id is None and (p.pinned or p.source == "user")
        ]
        for point in manual:
            try:
                near_places = self.repos.places.nearby(
                    point.lat, point.lon, radius_km=0.06, limit=1
                )
            except Exception as exc:
                log.warning("context: hand-placed stop lookup failed: %s", exc)
                continue
            for place in near_places:
                if place.id is not None and place.id not in {r.id for r in base_rows}:
                    base_rows.append(place)
                    log.info(
                        "context: hand-placed stop «%s» matched to «%s»",
                        point.name, place.name,
                    )

        seen: set[int] = set()
        base: list[Candidate] = []
        for row in base_rows:
            if row.id is None or row.id in seen:
                continue
            seen.add(row.id)
            base.append(candidate_of(row, 0.0))
        return base

    def _refinement_removals(
        self,
        directive,
        base: list[Candidate],
        excluded: set[int],
    ) -> set[int]:
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

    def _refinement_additions(
        self, directive, base: list[Candidate]
    ) -> list[Candidate]:
        """Stops the instruction asks to ADD, taken near the route.

        Convenience categories come from the stops' neighbourhood, not a global ranking.
        """
        found: dict[int, Candidate] = {}
        base_ids = {c.id for c in base}

        try:
            convenience = {
                c for c in directive.add_categories
                if c in constants.CONVENIENCE_CATEGORIES
            }
            if convenience:
                for c in _nearby_convenience(self.repos.places, base, convenience):
                    found[c.id] = c

            sights = {
                c for c in directive.add_categories
                if c not in constants.CONVENIENCE_CATEGORIES
            }
            if sights:
                for c in _nearby_convenience(
                    self.repos.places, base, sights,
                    radius_m=constants.CONVENIENCE_RADIUS_M * 4,
                    max_added=constants.CONVENIENCE_MAX_ADDED,
                ):
                    found.setdefault(c.id, c)
        except Exception as exc:
            log.warning("refinement: nearby add lookup failed: %s", exc)

        for name in directive.add_names:
            try:
                rows = self.repos.places.name_match(name, limit=1)
            except Exception as exc:
                log.warning("refinement: named add lookup failed: %s", exc)
                continue
            for place, _similarity in rows:
                if place.id is None or place.id in base_ids or place.id in found:
                    continue
                found[place.id] = candidate_of(place, 0.0)

        return [c for c in found.values() if c.id not in base_ids]

    def _generate_refinement(
        self, req: GenerateReq, base: list[Candidate], t0: float
    ) -> RouteResponse:
        """Apply ONE refinement operation to the route the user already has.

        The base stops are kept unless explicitly removed; never 422.
        """
        ctx = req.context
        assert ctx is not None
        instruction = (ctx.instruction or "").strip()
        excluded = set(ctx.excluded_ids or [])
        directive = interpret_refinement(instruction)

        removed = self._refinement_removals(directive, base, excluded)
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
            additions = self._refinement_additions(directive, kept)
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

        requirements = build_requirements(instruction or req.query, req, repos=self.repos)
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
            places=self.repos.places,
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

        turn = _Turn(req=req, t0=t0, costing=costing, tail_mode="refine", plan_info=info)
        _candidates, _cost, route, _info, plan = self._plan_tail(turn, route, constraints)
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
            _services_along_evidence(self.repos.places, requirements, shape),
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
            directive.operation, directive.reason_code, ms,
            len(base), len(route), len(additions),
        )
        trace.record(
            "response",
            input={"plan_status": status, "stops": len(route)},
            plan_status=status,
            stops=len(route),
            ms=ms,
            route=[c.name for c in route],
        )

        resp = self._build_response(
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

    def _build_response(
        self,
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


_build_response = Pipeline._build_response

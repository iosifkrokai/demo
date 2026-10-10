"""Pipeline orchestrator.

Wires the 10 steps together; one instance, reused across requests.
"""

from __future__ import annotations

import logging
import time as _time
from typing import Any

from core import constants
from core.config import openrouter_api_key
from core.errors import (
    NoCandidatesFound,
    UpstreamUnavailable,
)
from db.store.registry import Repositories
from embeddings.query import embed_query
from planner.models import (
    Candidate,
    GenerateReq,
    IntentDecision,
    IntentResult,
    OverallStatus,
    ParsedQuery,
    ResolvedConstraints,
    RouteResponse,
    RouteSummary,
)
from planner.valhalla.http import ping as valhalla_ping
from telemetry import progress, trace

from .catalogue import catalogue_response
from .cost import (
    _is_service_code,
    _is_sight_stop,
)
from .coverage import _outside_left_unresolved, refuse_out_of_coverage
from .dedupe import _drop_duplicates, _dupe_pairs
from .diversity import mmr_select
from .explain import explain as explain_route
from .geo import (
    _TRACE_NAMES_MAX,
    _geo_focus,
    _geo_focus_report,
    should_skip_geo_focus,
)
from .intent import build_requirements, intent_from_requirements
from .plan_tail import run_plan_tail
from .preprocess import preprocess
from .refine import (
    _context_changes,
    _drop_excluded,
)
from .refinement_flow import generate_refinement, refinement_base
from .render import render
from .resolve import resolve
from .response import (
    _gone,
    _names,
    _render_tour,
    _services_along_evidence,
    _to_places,
    _verdicts,
    build_response,
)
from .retrieve import apply_negative_filter, candidate_of, retrieve
from .turn import Turn, seconds_left
from .verify import overall_status, verify

log = logging.getLogger(__name__)


class Pipeline:
    """Stateless planner. One instance, reused across requests."""

    def __init__(self, repos: Repositories):
        self.repos = repos

    def generate(self, req: GenerateReq) -> RouteResponse:
        turn = Turn(req=req, t0=_time.perf_counter())
        trace.record(
            "query",
            input=req.query,
            query=req.query,
            profile=req.profile,
            time_budget_minutes=req.time_budget_minutes,
            origin=bool(req.origin),
        )

        if req.context and req.context.base_points:
            base = refinement_base(self.repos, req.context)
            if base:
                trace.record(
                    "refinement",
                    input=[p.name for p in req.context.base_points][:_TRACE_NAMES_MAX],
                    base_points=len(req.context.base_points),
                )
                return generate_refinement(req, base, turn.t0, repos=self.repos)

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

        return build_response(
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

    def _step_preprocess(self, turn: Turn):
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

    def _step_interpret(self, turn: Turn, pre):
        progress.note(progress.STAGE_INTERPRETING)
        requirements = build_requirements(
            turn.req.query, turn.req, repos=self.repos, wall_clock_s=seconds_left(turn.t0)
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

    def _step_resolve(self, turn: Turn, requirements, intent):
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
        self, turn: Turn, constraints: ResolvedConstraints
    ) -> list[Candidate]:
        qvec = embed_query(turn.req.query)
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
        self, turn: Turn, candidates: list[Candidate]
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
        turn: Turn,
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
        turn: Turn,
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
        turn: Turn,
        candidates: list[Candidate],
        constraints: ResolvedConstraints,
    ):
        turn.base_candidates = []

        turn.costing = turn.req.profile or ("auto" if turn.region_scope else "pedestrian")

        left = seconds_left(turn.t0)
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

        _candidates, _cost, _route, _info, plan = run_plan_tail(
            turn, candidates, constraints
        )
        return plan

    def _step_render(self, turn: Turn, plan) -> dict:
        progress.note(progress.STAGE_DRAWING)
        if seconds_left(turn.t0) >= constants.RENDER_MIN_LEFT_S:
            shape, summary = _render_tour(
                plan.route,
                costing=turn.costing,
                origin=turn.req.origin,
                round_trip=turn.round_trip,
            )
        else:
            log.info("deadline: %.1fs left — skipping geometry", seconds_left(turn.t0))
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

    def _step_explain(self, turn: Turn, plan, shape: dict) -> str:
        explanation = explain_route(plan.route, plan.trace, turn.walk_s, turn.costing)
        trace.record(
            "explain",
            input=[c.name for c in plan.route],
            output=explanation,
        )
        return explanation

    def _step_verify(self, turn: Turn, plan, requirements) -> OverallStatus:
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
            req, requirements, intent, constraints, names, t0
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

        turn = Turn(
            req=GenerateReq(query="точки пользователя"),
            t0=_time.perf_counter(),
            costing=profile or "pedestrian",
            tail_mode="reroute",
        )
        _candidates, _cost, _route, _info, plan = run_plan_tail(
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

        from embeddings import model

        embedder_ok = model.is_available()
        llm_ok = bool(openrouter_api_key())

        return {
            # A missing reader is not "ok": nothing can be planned without one.
            "status": "ok" if (db_ok and valhalla_ok and embedder_ok and llm_ok) else "degraded",
            "embedder": embedder_ok,
            "llm": llm_ok,
            "db": db_ok,
            "valhalla": valhalla_ok,
        }

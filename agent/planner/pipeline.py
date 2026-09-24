"""Pipeline orchestrator.

Wires together the 10 steps. One instance, reused across requests, with
explicit dependencies (embedder, db) for testability.

Steps in order:
  0  preprocess         query → PreprocessedQuery
  1  extract_intent     query → IntentResult
  2  resolve            IntentResult + client params → ResolvedConstraints
  -- embed query         text → vec
  3  retrieve           vec + constraints → list[Candidate] (RRF-fused)
  3.5 rerank            candidates → top-K (cross-encoder)
  4  diversity          candidates → top-N (MMR)
  5  cost               candidates + constraints → CostMatrix
  6  optimize           candidates + cost → ordered list (3 modes)
  7  validate           ordered + cost → ValidatedPlan
  8  render             ValidatedPlan → shape + summary (Valhalla /route)
  9  explain            ValidatedPlan + summary → human-readable string

Returns: RouteResponse (Pydantic) — what main.py serves over HTTP.
"""

from __future__ import annotations

import logging
import time as _time

import psycopg
from fastembed import TextEmbedding

from ..config import settings
from ..errors import (
    NoCandidatesFound,
    NoRoutePossible,
    UpstreamUnavailable,
)
from ..models import (
    BudgetInfo,
    GenerateReq,
    ParsedQuery,
    Place,
    RouteResponse,
    RouteSummary,
)
from ..valhalla_client import ping as valhalla_ping
from .cost import visit_time_minutes
from .cost import compute_cost_matrix
from .diversity import mmr_select
from .explain import explain as explain_route
from .intent import extract_intent
from .optimize import optimize
from .preprocess import preprocess
from .render import render
from .rerank import rerank as rerank_pool
from .resolve import resolve
from .retrieve import retrieve
from .validate import validate

log = logging.getLogger(__name__)


class Pipeline:
    """Stateless planner. One instance, reused across requests."""

    def __init__(self, embedder: TextEmbedding, db: psycopg.Connection):
        self.embedder = embedder
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, req: GenerateReq) -> RouteResponse:
        t0 = _time.perf_counter()

        # 0. Preprocess
        pre = preprocess(req.query)

        # 1. Intent
        intent = extract_intent(req.query)

        # 2. Resolve
        constraints = resolve(
            intent,
            explicit_time_budget=req.time_budget_minutes,
            explicit_bbox=req.region_bbox,
            db=self.db,
        )

        # Embed query (used for vector retrieval)
        qvec = self._embed(req.query)

        # 3. Retrieve
        candidates = retrieve(
            constraints,
            qvec,
            self.db,
            query_text=req.query,
        )
        if not candidates:
            raise NoCandidatesFound(
                "no candidates matched the query — попробуйте другую формулировку"
            )

        # 3.5 Rerank
        candidates = rerank_pool(req.query, candidates, top_k=settings.RERANK_POOL_SIZE)

        # 4. MMR diversity
        mmr_pool_size = min(settings.MMR_POOL_SIZE, len(candidates))
        candidates = mmr_select(
            candidates,
            n=mmr_pool_size,
            db=self.db,
            constraints=constraints,
        )
        if len(candidates) < 2:
            raise NoCandidatesFound(
                f"only {len(candidates)} candidate(s) survived diversity filter"
            )

        # 5. Cost matrix
        cost = compute_cost_matrix(candidates, constraints)

        # 6. Optimize
        route, info = optimize(candidates, cost, constraints)
        if len(route) < 2:
            raise NoRoutePossible("optimizer could not produce a route with ≥ 2 stops")

        # 7. Validate
        plan = validate(route, cost, constraints, info)

        # 8. Render (Valhalla /route)
        try:
            shape, summary = render(plan.route)
        except UpstreamUnavailable:
            shape, summary = {}, {}

        walk_s = float(summary.get("time", 0.0)) if summary else 0.0
        length_km = summary.get("length") if summary else None

        # If Valhalla /route failed but /sources_to_targets succeeded,
        # we still have walk_s from cost matrix — use it as fallback.
        if walk_s == 0.0 and plan.walk_seconds > 0:
            walk_s = plan.walk_seconds
            length_km = length_km  # may be None

        # 9. Explain
        explanation = explain_route(plan.route, plan.trace, walk_s)

        ms = int((_time.perf_counter() - t0) * 1000)
        log.info(
            "pipeline.ok query_len=%d ms=%d n_stops=%d walk_s=%.0f budget_min=%d source=%s",
            len(req.query), ms, len(plan.route), walk_s,
            constraints.time_budget_minutes, intent.source,
        )

        return self._build_response(
            intent=intent,
            constraints=constraints,
            plan=plan,
            shape=shape,
            walk_s=walk_s,
            length_km=length_km,
            explanation=explanation,
        )

    def reroute(self, point_ids: list[int]) -> RouteResponse:
        """Re-route a chosen list of place IDs (same as old /routes/reroute)."""
        from ..search import fetch_points_by_ids
        rows = fetch_points_by_ids(self.db, point_ids)
        if len(rows) != len(point_ids):
            missing = set(point_ids) - {r["id"] for r in rows}
            raise NoCandidatesFound(f"unknown point_ids: {sorted(missing)}")

        from ..models import Candidate
        candidates = [
            Candidate(
                id=r["id"], name=r["name"], category=r.get("category"),
                lat=r["lat"], lon=r["lon"], blurb=r.get("blurb"),
                fun_fact=r.get("fun_fact"),
                relevance=1.0,
            )
            for r in rows
        ]
        constraints = resolve(
            extract_intent("точки пользователя"),
            explicit_time_budget=None,
            explicit_bbox=None,
            db=self.db,
        )
        constraints.time_budget_minutes = settings.MAX_TIME_BUDGET_MIN
        constraints.must_visit_ids = list(point_ids)

        cost = compute_cost_matrix(candidates, constraints)
        route, info = optimize(candidates, cost, constraints)
        plan = validate(route, cost, constraints, info)

        try:
            shape, summary = render(plan.route)
        except UpstreamUnavailable:
            shape, summary = {}, {}
        walk_s = float((summary or {}).get("time", 0.0)) or plan.walk_seconds
        length_km = (summary or {}).get("length")

        return RouteResponse(
            parsed=ParsedQuery(source="explicit"),
            points=[
                Place(
                    id=p.id, name=p.name, category=p.category,
                    lat=p.lat, lon=p.lon, blurb=p.blurb, fun_fact=p.fun_fact,
                    visit_minutes=visit_time_minutes(p.category),
                )
                for p in plan.route
            ],
            shape=shape,
            summary=RouteSummary(length_km=length_km, time_seconds=walk_s),
            budget=None,
            explanation=explain_route(plan.route, plan.trace, walk_s),
        )

    def explain_route(self, point_ids: list[int]) -> str:
        """Natural-language Russian description of a list of points."""
        from ..search import fetch_points_by_ids
        rows = fetch_points_by_ids(self.db, point_ids)
        if len(rows) != len(point_ids):
            missing = set(point_ids) - {r["id"] for r in rows}
            raise NoCandidatesFound(f"unknown point_ids: {sorted(missing)}")

        from ..models import Candidate
        cands = [
            Candidate(
                id=r["id"], name=r["name"], category=r.get("category"),
                lat=r["lat"], lon=r["lon"], blurb=r.get("blurb"),
                fun_fact=r.get("fun_fact"),
            )
            for r in rows
        ]
        # Just list them — no re-ordering for explicit lists.
        trace = {"algorithm": "direct", "diversity": 1.0, "fits_budget": True}
        return explain_route(cands, trace, walk_seconds=0.0)

    def health(self) -> dict:
        """Cheap liveness/readiness snapshot."""
        db_ok = False
        try:
            with self.db.cursor() as cur:
                cur.execute("SELECT 1")
                db_ok = cur.fetchone() is not None
        except Exception as e:
            log.warning("health.db err=%s", e)

        try:
            valhalla_ok = valhalla_ping()
        except Exception:
            valhalla_ok = False

        # embedder is always loaded (lifespan waits for it)
        embedder_ok = self.embedder is not None
        # intent backend = Gemini via DeepInfra (always "ready" if key is set)
        intent_ok = bool(__import__("os").environ.get("DEEPINFRA_API_KEY"))

        return {
            "status": "ok" if (embedder_ok and db_ok and valhalla_ok and intent_ok) else "degraded",
            "embedder": embedder_ok,
            "llm": intent_ok,
            "db": db_ok,
            "valhalla": valhalla_ok,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _embed(self, text: str) -> list[float]:
        arr = list(self.embedder.embed([text]))[0]
        return arr.tolist() if hasattr(arr, "tolist") else list(arr)

    def _build_response(
        *,
        intent,
        constraints,
        plan,
        shape: dict,
        walk_s: float,
        length_km: float | None,
        explanation: str,
    ) -> RouteResponse:
        d = intent.decision
        return RouteResponse(
            parsed=ParsedQuery(
                keywords=d.keywords_pos,
                categories=d.categories_pos,
                time_budget_minutes=d.time_budget_minutes,
                source=intent.source,
            ),
            points=[
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
                    visit_minutes=visit_time_minutes(p.category),
                )
                for p in plan.route
            ],
            shape=shape,
            summary=RouteSummary(length_km=length_km, time_seconds=walk_s),
            budget=BudgetInfo(
                budget_minutes=constraints.time_budget_minutes,
                walk_minutes=int(walk_s / 60) + 1,
                visit_minutes=plan.visit_seconds // 60,
                total_minutes=plan.total_seconds // 60,
                fits=plan.fits_budget,
                stops_dropped=plan.stops_dropped,
            ),
            explanation=explanation,
            debug={
                "intent_source": intent.source,
                "intent_latency_ms": intent.latency_ms,
                "constraints": {
                    "must_visit_ids": constraints.must_visit_ids,
                    "optional_categories": constraints.optional_categories,
                    "forbidden_categories": constraints.forbidden_categories,
                    "time_budget_minutes": constraints.time_budget_minutes,
                },
                "trace": plan.trace,
            },
        )

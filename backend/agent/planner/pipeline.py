"""Pipeline orchestrator.

Wires together the 10 steps. One instance, reused across requests, with
explicit dependencies (db) for testability.

Steps in order:
  0  preprocess         query → PreprocessedQuery
  1  extract_intent     query → IntentResult
  2  resolve            IntentResult + client params → ResolvedConstraints
  -- embed query         text → vec (OpenRouter)
  3  retrieve           vec + constraints → list[Candidate] (RRF-fused)
  3.5 rerank            candidates → top-K (OpenRouter rerank)
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
import re as _re
import time as _time

import httpx
import psycopg

from .. import constants
from ..errors import (
    NoCandidatesFound,
    NoRoutePossible,
    UpstreamUnavailable,
)
from ..models import (
    BudgetInfo,
    Candidate,
    GenerateReq,
    LatLon,
    ParsedQuery,
    Place,
    RouteResponse,
    RouteSummary,
)
from ..search import fetch_points_by_ids
from ..valhalla_client import ping as valhalla_ping
from .cost import compute_cost_matrix, visit_time_minutes
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


def _openrouter_embed(texts: list[str]) -> list[list[float]]:
    """Call OpenRouter embeddings API. Returns list of embedding vectors."""
    api_key = __import__("os").environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            "https://openrouter.ai/api/v1/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": constants.EMBED_MODEL,
                "input": texts,
            },
        )
        r.raise_for_status()
        body = r.json()
        return [item["embedding"] for item in body["data"]]


def _geo_focus(
    candidates: list,
    origin: LatLon | None = None,
    anchor_id: int | None = None,
) -> list:
    """Drop candidates too far from the anchor.

    Anchor priority: tourist GPS > must-visit/named place > top-RRF candidate.
    Keyword-mode retrieval (no embeddings) can surface matching places from
    across the whole voblast; a walking route must stay local.
    """

    if not candidates:
        return []
    if origin is not None:
        anchor = origin
        dist = lambda c: _distance_from_origin_m(c, origin)
    elif anchor_id is not None and any(c.id == anchor_id for c in candidates):
        anchor = next(c for c in candidates if c.id == anchor_id)
        dist = lambda c: _distance_m(c, anchor)
    else:
        anchor = max(candidates, key=lambda c: c.rrf_score)
        dist = lambda c: _distance_m(c, anchor)
    max_m = constants.GEO_FOCUS_KM * 1000

    if origin is not None or anchor_id is not None:
        # A known position (tourist GPS) or a named place anchors the walk:
        # never inflate the radius here.  Doubling used to pull 50 km-away
        # castles into "Мирский замок", and the optimizer then dropped
        # everything but one stop because no leg was walkable.
        keep = [c for c in candidates if c is anchor or dist(c) <= max_m]
        return keep

    while True:
        keep = [
            c for c in candidates
            if c is anchor or dist(c) <= max_m
        ]
        if len(keep) >= 3 or max_m > constants.GEO_FOCUS_MAX_KM * 1000:
            return keep
        max_m *= 2


def _distance_m(a, b) -> float:
    """Equirectangular distance in metres (fine at city scale)."""
    return _distance_pt_m(a.lat, a.lon, b.lat, b.lon)


def _distance_pt_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular distance in metres (fine at city scale)."""
    import math

    lat_mid = math.radians((lat1 + lat2) / 2)
    dx = math.radians(lon1 - lon2) * 6_371_000 * math.cos(lat_mid)
    dy = math.radians(lat1 - lat2) * 6_371_000
    return math.hypot(dx, dy)


def _distance_from_origin_m(c, origin: LatLon) -> float:
    return _distance_pt_m(c.lat, c.lon, origin.lat, origin.lon)


def _norm_name(name: str) -> str:
    """Lowercase, drop parentheticals and punctuation — for POI-name matching."""
    n = _re.sub(r"\([^)]*\)", " ", (name or "").lower())
    n = _re.sub(r"[^0-9a-zа-яё]+", " ", n)
    return " ".join(n.split())


def _drop_duplicates(candidates: list[Candidate], radius_m: float) -> list[Candidate]:
    """Drop places that sit on top of an already-kept, better-ranked place.

    The base stores the same POI more than once whenever a curated row and an
    OSM row disagree on the name or the precise coordinates ("Новый замок
    (дворец Стефана Батория)" vs "Новый замок"; "Дом-музей Адама Мицкевича"
    twice, 340 m apart), which put the same sight into a route twice.
    Candidates arrive relevance-ordered, so the highest-ranked member of each
    cluster wins.  A candidate is dropped when it is within `radius_m` of a
    kept place, or when it carries the same normalised name and lies within
    `constants.DUPLICATE_NAME_RADIUS_M`.
    """
    kept: list[Candidate] = []
    for cand in candidates:
        cand_name = _norm_name(cand.name)
        duplicate = False
        for k in kept:
            dist = _distance_pt_m(cand.lat, cand.lon, k.lat, k.lon)
            if dist < radius_m:
                duplicate = True
                break
            if (
                cand_name
                and cand_name == _norm_name(k.name)
                and dist < constants.DUPLICATE_NAME_RADIUS_M
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(cand)
    return kept


class Pipeline:
    """Stateless planner. One instance, reused across requests."""

    def __init__(self, db: psycopg.Connection):
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

        # Embed query (OpenRouter). Without an API key the pipeline degrades
        # gracefully: retrieval falls back to the keyword signal only.
        api_key = __import__("os").environ.get("OPENROUTER_API_KEY")
        qvec = _openrouter_embed([req.query])[0] if api_key else []
        if not qvec:
            log.warning("no OPENROUTER_API_KEY — vector retrieval disabled, keyword mode")

        # Anchor point for locality: the tourist's GPS start, else the row behind
        # the named town (area_anchor), else the must-visit POI.  Retrieval uses
        # it so the pool really contains places around that point.
        geo_anchor = constraints.area_anchor or (
            constraints.must_visit_ids[0] if constraints.must_visit_ids else None
        )
        near: tuple[float, float] | None = None
        if req.origin is not None:
            near = (req.origin.lat, req.origin.lon)
        elif geo_anchor is not None:
            anchor_rows = fetch_points_by_ids(self.db, [geo_anchor])
            if anchor_rows:
                near = (float(anchor_rows[0]["lat"]), float(anchor_rows[0]["lon"]))

        # 3. Retrieve
        candidates = retrieve(
            constraints,
            qvec,
            self.db,
            query_text=req.query,
            near=near,
        )
        if not candidates:
            raise NoCandidatesFound(
                "no candidates matched the query — попробуйте другую формулировку"
            )

        # 3.5 Rerank (OpenRouter) — needs the API key; skipped in keyword mode.
        if api_key:
            candidates = rerank_pool(req.query, candidates, top_k=constants.RERANK_POOL_SIZE)

        # 3.55 Physical duplicates: the same POI exists twice when the curated
        # row and the OSM row disagree on the name ("Новый замок (дворец
        # Стефана Батория)" vs "Новый замок").  Without this the route visits
        # one castle twice under two names.  Candidates are relevance-ordered
        # here, so the best-ranked row of each cluster survives.
        candidates = _drop_duplicates(candidates, constants.DUPLICATE_RADIUS_M)

        # 3.6 Geographic focus: keep the route walkable — candidates beyond
        # GEO_FOCUS_KM from the tourist's position (or the top-scored hit when
        # position is unknown) are dropped (radius doubles if that leaves <3).
        # Use area_anchor (town/district geo anchor) when available — this keeps
        # the geo focus on the correct town without forcing an arbitrary POI as must-visit.
        # Fall back to must_visit_ids only when there is no separate area anchor.
        geo_anchor = constraints.area_anchor or (
            constraints.must_visit_ids[0] if constraints.must_visit_ids else None
        )
        candidates = _geo_focus(candidates, origin=req.origin, anchor_id=geo_anchor)
        if len(candidates) < 2:
            raise NoCandidatesFound(
                "все кандидаты слишком далеко друг от друга — уточните город или район"
            )

        # 4. MMR diversity
        mmr_pool_size = min(constants.MMR_POOL_SIZE, len(candidates))
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

        # Transport mode from the request (webapp profile picker); the
        # cost matrix AND the rendered shape must use the same costing.
        costing = req.profile or "pedestrian"

        # 5. Cost matrix (pre-filter may drop some candidates — align the list)
        cost = compute_cost_matrix(candidates, constraints, costing=costing)
        if cost.indices != list(range(len(candidates))):
            candidates = [candidates[i] for i in cost.indices]

        # 6. Optimize. With a known tourist position the route must START there:
        # the optimizer scores orders over the candidate matrix; we prepend the
        # origin afterwards as a fixed first leg (render walks origin → first stop).
        route, info = optimize(candidates, cost, constraints)
        if len(route) < 2:
            raise NoRoutePossible("optimizer could not produce a route with ≥ 2 stops")

        # 7. Validate
        plan = validate(route, cost, constraints, info)

        # 8. Render (Valhalla /route) — origin is the tourist's GPS start
        try:
            shape, summary = render(plan.route, costing=costing, origin=req.origin)
        except UpstreamUnavailable:
            shape, summary = {}, {}

        walk_s = float(summary.get("time", 0.0)) if summary else 0.0
        length_km = summary.get("length") if summary else None

        if walk_s == 0.0 and plan.walk_seconds > 0:
            walk_s = plan.walk_seconds

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

    def reroute(self, point_ids: list[int], profile: str | None = None) -> RouteResponse:
        """Re-route a chosen list of place IDs."""
        from ..search import fetch_points_by_ids
        rows = fetch_points_by_ids(self.db, point_ids)
        if len(rows) != len(point_ids):
            missing = set(point_ids) - {r["id"] for r in rows}
            raise NoCandidatesFound(f"unknown point_ids: {sorted(missing)}")

        from .retrieve import _row_to_candidate
        candidates = [_row_to_candidate(r, 1.0) for r in rows]
        constraints = resolve(
            extract_intent("точки пользователя"),
            explicit_time_budget=None,
            explicit_bbox=None,
            db=self.db,
        )
        constraints.time_budget_minutes = constants.MAX_BUDGET_MIN
        constraints.must_visit_ids = list(point_ids)

        cost = compute_cost_matrix(candidates, constraints, costing=profile or "pedestrian")
        if cost.indices != list(range(len(candidates))):
            candidates = [candidates[i] for i in cost.indices]
        route, info = optimize(candidates, cost, constraints)
        plan = validate(route, cost, constraints, info)

        try:
            shape, summary = render(plan.route, costing=profile or "pedestrian")
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
                    fun_facts=p.fun_facts, links=p.links,
                    opening_hours=p.opening_hours, ticket_price=p.ticket_price,
                    town=p.town, district=p.district,
                    visit_minutes=p.visit_minutes_db or visit_time_minutes(p.category),
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

        from .retrieve import _row_to_candidate
        cands = [_row_to_candidate(r, 1.0) for r in rows]
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

        openrouter_ok = bool(__import__("os").environ.get("OPENROUTER_API_KEY"))

        return {
            "status": "ok" if (db_ok and valhalla_ok and openrouter_ok) else "degraded",
            "embedder": openrouter_ok,  # embeddings + intent share the OpenRouter key
            "llm": openrouter_ok,
            "db": db_ok,
            "valhalla": valhalla_ok,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_response(
        self,
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
                    opening_hours=p.opening_hours,
                    ticket_price=p.ticket_price,
                    town=p.town,
                    district=p.district,
                    visit_minutes=p.visit_minutes_db or visit_time_minutes(p.category),
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
                    "area_anchor": constraints.area_anchor,
                    "optional_categories": constraints.optional_categories,
                    "forbidden_categories": constraints.forbidden_categories,
                    "time_budget_minutes": constraints.time_budget_minutes,
                },
                "trace": plan.trace,
            },
        )

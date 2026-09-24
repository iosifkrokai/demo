"""RoutePlanner — the orchestration layer.

This is the brain of the agent. It takes a GenerateReq and produces a fully
shaped RouteResponse. The HTTP layer in main.py is a thin wrapper around it.

Why a class:
  - Explicit dependencies (embedder, db, valhalla, llm) — easy to mock in tests.
  - State is contained; no module-level globals to fight FastAPI lifespan on.
  - One place to add cross-cutting concerns (logging, metrics, caching).
"""

from __future__ import annotations

import logging
import time as _time
from typing import Any

import psycopg
from fastembed import TextEmbedding

from .config import settings
from .errors import InvalidRequest, NoCandidatesFound, NoRoutePossible, UpstreamUnavailable
from .llm import llm_is_ready, parse_query
from .models import (
    BudgetInfo,
    Candidate,
    GenerateReq,
    ParsedQuery,
    Place,
    RouteResponse,
    RouteSummary,
)
from .routing import (
    build_latlons,
    optimise_route,
    visit_time_minutes,
)
from .search import _keyword_search, candidates_by_embedding, fetch_points_by_ids
from .rerank import rerank
from .valhalla_client import ping as valhalla_ping
from .valhalla_client import route_through, time_matrix

log = logging.getLogger(__name__)


class RoutePlanner:
    """Stateless planner. One instance, reused across requests."""

    def __init__(self, embedder: TextEmbedding, db: psycopg.Connection):
        self.embedder = embedder
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, req: GenerateReq) -> RouteResponse:
        t0 = _time.perf_counter()
        log.info("generate.start", extra={"query_len": len(req.query),
                                          "explicit_budget": req.time_budget_minutes})

        parsed = self._parse_intent(req)
        # Budget is the only control knob now. Default: 2 hours for a typical walk.
        budget_min = self._resolve_budget(parsed, req.time_budget_minutes)
        # Upper bound for retrieval pool: agent decides how many fit the budget.
        # Derive max stops from budget: avg 15 min per stop (walk + visit), cap at 20.
        # Let the budget be the only knob; the planner fills it up to this ceiling.
        n = min(20, max(3, budget_min // 15))

        candidates = self._find_candidates(req.query, parsed.categories, n)
        if len(candidates) < 2:
            raise NoCandidatesFound(
                f"only {len(candidates)} candidate(s) match the query; need ≥ 2"
            )

        # Plan the order within the budget. optimise_route handles shrinking
        # internally via the worst-neighbour heuristic (routing.py).
        ordered, info = self._plan_order(candidates, n=n, budget_min=budget_min)

        # LLM re-ranker: the model may want to prefer/drop/swap points
        # to better match the query intent (e.g. "museums but no churches").
        # If ops are returned, rebuild the route with the new ids.
        plan_ids = ordered  # type: ignore[assignment]
        rerank_result = rerank(
            req.query,
            _as_point_dicts(ordered),
            _as_point_dicts(candidates),
            {"walk_minutes": int(budget_min), "length": None},
            time_budget_minutes=budget_min,
        )
        if rerank_result["ids"] is not None:
            # Remap ids back to Candidate objects and rebuild
            id_to_candidate = {c.id: c for c in candidates}
            plan_ids = [id_to_candidate[i] for i in rerank_result["ids"] if i in id_to_candidate]
            if len(plan_ids) >= 2:
                log.info("rerank.applied", extra={"ops": rerank_result["applied"]})
            else:
                plan_ids = ordered  # fall back if rerank killed the route
        else:
            plan_ids = ordered

        # Final canonical route (with directions shape) from Valhalla.
        locations = [{"lat": p.lat, "lon": p.lon,
                      "type": "break" if i in (0, len(plan_ids) - 1) else "via"}
                     for i, p in enumerate(plan_ids)]
        shape, summary = route_through(locations)
        if summary is None:
            raise UpstreamUnavailable("valhalla /route returned no summary")

        walk_s = float(summary.get("time", 0.0))
        stops_dropped = info.get("stops_dropped", 0)

        budget_info = self._budget_summary(list(plan_ids), walk_s, budget_min, stops_dropped)

        ms = int((_time.perf_counter() - t0) * 1000)
        log.info("generate.ok", extra={
            "ms": ms,
            "n_stops": len(ordered),
            "walk_seconds": walk_s,
            "budget_minutes": budget_min,
        })

        return RouteResponse(
            parsed=parsed,
            points=[
                Place(
                    id=p.id, name=p.name, category=p.category, lat=p.lat, lon=p.lon,
                    blurb=p.blurb, fun_fact=p.fun_fact,
                    fun_facts=p.fun_facts,
                    links=p.links,
                    visit_minutes=visit_time_minutes(p.category),
                ) for p in plan_ids
            ],
            shape=shape,
            summary=RouteSummary(
                length_km=summary.get("length"),
                time_seconds=walk_s,
            ),
            budget=budget_info,
        )

    def reroute(self, point_ids: list[int]) -> RouteResponse:
        rows = fetch_points_by_ids(self.db, point_ids)
        if len(rows) != len(point_ids):
            missing = set(point_ids) - {r["id"] for r in rows}
            raise NoCandidatesFound(f"unknown point_ids: {sorted(missing)}")

        candidates = [Candidate(id=r["id"], name=r["name"], category=r["category"],
                                lat=r["lat"], lon=r["lon"], blurb=r.get("blurb"),
                                fun_fact=r.get("fun_fact"))
                      for r in rows]
        ordered, _ = self._plan_order(candidates, n=len(candidates), budget_min=None)

        locations = [{"lat": p.lat, "lon": p.lon,
                      "type": "break" if i in (0, len(ordered) - 1) else "via"}
                     for i, p in enumerate(ordered)]
        shape, summary = route_through(locations)
        walk_s = float((summary or {}).get("time", 0.0))

        return RouteResponse(
            parsed=ParsedQuery(source="explicit"),
            points=[
                Place(id=p.id, name=p.name, category=p.category, lat=p.lat, lon=p.lon,
                      blurb=p.blurb, fun_fact=p.fun_fact,
                      fun_facts=p.fun_facts, links=p.links,
                      visit_minutes=visit_time_minutes(p.category))
                for p in ordered
            ],
            shape=shape,
            summary=RouteSummary(
                length_km=(summary or {}).get("length"),
                time_seconds=walk_s,
            ),
        )

    def explain(self, point_ids: list[int]) -> str:
        """Natural-language Russian description of a list of points."""
        rows = fetch_points_by_ids(self.db, point_ids)
        if len(rows) != len(point_ids):
            missing = set(point_ids) - {r["id"] for r in rows}
            raise NoCandidatesFound(f"unknown point_ids: {sorted(missing)}")

        parts: list[str] = []
        for i, r in enumerate(rows, start=1):
            head = f"{i}. {r['name']}"
            if r.get("category"):
                head += f" ({r['category']})"
            if r.get("blurb"):
                head += f" — {r['blurb']}"
            parts.append(head)
        return "Маршрут пешком по Гродно:\n" + "\n".join(parts)

    def health(self) -> dict:
        """Cheap liveness/readiness snapshot."""
        db_ok = False
        try:
            with self.db.cursor() as cur:
                cur.execute("SELECT 1")
                db_ok = cur.fetchone() is not None
        except Exception as e:
            log.warning("health.db", extra={"err": str(e)})

        try:
            valhalla_ok = valhalla_ping()
        except Exception:
            valhalla_ok = False

        return {
            "status": "ok" if (self.embedder is not None and db_ok and valhalla_ok) else "degraded",
            "embedder": self.embedder is not None,
            "llm": llm_is_ready(),
            "db": db_ok,
            "valhalla": valhalla_ok,
        }

    # ------------------------------------------------------------------
    # Steps (private)
    # ------------------------------------------------------------------

    def _parse_intent(self, req: GenerateReq) -> ParsedQuery:
        """Ask the LLM to extract intent, then normalise the result.

        The LLM never overrides an explicit client request — that contract is
        enforced here, not by magic numbers in the call sites.
        """
        raw = parse_query(req.query, default_n_points=settings.DEFAULT_BUDGET_MIN)
        return ParsedQuery(
            keywords=raw.get("keywords") or [],
            categories=raw.get("categories") or [],
            time_budget_minutes=raw.get("time_budget_minutes"),
            source="llm" if raw.get("source") != "fallback" else "fallback",
        )

    def _resolve_budget(self, parsed: ParsedQuery, explicit: int | None) -> int:
        """Resolve time budget. Defaults to 120 min (2-hour walk) if unspecified."""
        if explicit is not None:
            return max(settings.MIN_BUDGET_MIN,
                       min(explicit, settings.MAX_BUDGET_MIN))
        if parsed.time_budget_minutes is not None:
            return max(settings.MIN_BUDGET_MIN,
                       min(int(parsed.time_budget_minutes), settings.MAX_BUDGET_MIN))
        return settings.DEFAULT_BUDGET_MIN

    def _find_candidates(self, query: str, categories: list[str], n: int) -> list[Candidate]:
        """Return a relevance-ranked list of candidate places.

        Hybrid retrieval:
        1. Vector search: top-N by cosine similarity — best for full sentences.
        2. Keyword search: exact name match — best for short queries where
           embedding model gives poor results ("горисполком" ≠ "Здание Горисполкома").

        Category preference is applied as a soft score boost, not a hard filter.
        """
        qvec = self._embed(query)
        pool_limit = max(settings.CANDIDATE_POOL_SIZE, n * 2)

        # Primary: vector search
        rows = candidates_by_embedding(
            self.db, qvec,
            limit=pool_limit,
        )

        # Hybrid fallback for short queries: also search by name keywords
        # (vector search is weak for 1-2 word queries)
        import re
        words = re.findall(r"[а-яёa-z]{3,}", query.lower())
        if len(words) < 3:
            kw_rows = _keyword_search(self.db, query, limit=pool_limit)
            seen_ids = {r["id"] for r in rows}
            for r in kw_rows:
                if r["id"] not in seen_ids:
                    rows.append(r)

        # Soft category scoring
        cands: list[Candidate] = []
        for r in rows:
            cos_dist = float(r.get("cosine_dist", 0.0))
            score = 1.0 - cos_dist
            if categories and r.get("category") in categories:
                score += 0.1  # category boost: soft preference, not filter
            # Parse fun_facts (pipe-delimited) and links (JSON) from DB
            fun_facts_raw = r.get("fun_facts") or ""
            fun_facts = [f.strip() for f in fun_facts_raw.split("|") if f.strip()][:3]
            # links stored as: {"title":"...","url":"..."}|{"title":"...","url":"..."}
            # Parse each pipe-delimited JSON object
            import json as _json
            links = []
            for raw in (r.get("links") or "").split("|"):
                raw = raw.strip()
                if raw:
                    try:
                        links.append(_json.loads(raw))
                    except Exception:
                        pass
            cands.append(Candidate(
                id=r["id"], name=r["name"], category=r.get("category"),
                lat=r["lat"], lon=r["lon"], blurb=r.get("blurb"),
                fun_fact=r.get("fun_fact"),
                fun_facts=fun_facts,
                links=links,
                relevance=score,
            ))
        # Sort by score descending, tie-break by id for determinism
        cands.sort(key=lambda c: (c.relevance, -c.id), reverse=True)
        return cands

    def _embed(self, text: str) -> list[float]:
        arr = list(self.embedder.embed([text]))[0]
        return arr.tolist() if hasattr(arr, "tolist") else list(arr)

    def _plan_order(self, candidates: list[Candidate], *,
                    n: int, budget_min: int | None) -> tuple[list[Candidate], dict]:
        """Compute the optimal ordering over the n best candidates.

        Returns (ordered_candidates, info). The matrix is computed only on
        the n we keep — not on the whole 50-strong pool.
        """
        if len(candidates) > n:
            candidates = candidates[:n]
        n = max(2, min(n, len(candidates)))

        if len(candidates) == 2:
            return candidates, {"algorithm": "direct", "n": n, "stops_dropped": 0}

        matrix = time_matrix(build_latlons(candidates), build_latlons(candidates))
        if not matrix or len(matrix) != n or any(len(row) != n for row in matrix):
            raise UpstreamUnavailable("valhalla time_matrix returned malformed shape")

        ordered, info = optimise_route(
            candidates, matrix,
            n=n,
            seed_first=True,
            time_budget_minutes=budget_min,
        )
        info["matrix_cells"] = n * n
        return ordered, info

    @staticmethod
    def _budget_summary(ordered: list[Candidate], walk_s: float,
                        budget_min: int, stops_dropped: int) -> BudgetInfo:
        visits_s = sum(visit_time_minutes(c.category) for c in ordered) * 60
        total_s = walk_s + visits_s
        return BudgetInfo(
            budget_minutes=budget_min,
            walk_minutes=int(walk_s / 60) + 1,
            visit_minutes=int(visits_s / 60),
            total_minutes=int(total_s / 60) + 1,
            fits=total_s <= budget_min * 60,
            stops_dropped=stops_dropped,
        )


def _as_point_dicts(candidates: list[Candidate]) -> list[dict]:
    """Convert Candidate objects to the plain dicts rerank.py expects."""
    return [
        {
            "id": c.id,
            "name": c.name,
            "category": c.category,
            "description": (c.blurb or "")[:180],
            "fun_fact": (c.fun_fact or "")[:120],
            "lat": c.lat,
            "lon": c.lon,
            "fun_facts": c.fun_facts,
            "links": c.links,
        }
        for c in candidates
    ]

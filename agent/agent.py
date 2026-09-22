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
from .search import candidates_by_embedding, db_categories, fetch_points_by_ids
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
                                          "explicit_n": req.n_points,
                                          "explicit_budget": req.time_budget_minutes})

        parsed = self._parse_intent(req)
        bbox = self._resolve_bbox(parsed, req.region_bbox)
        n = self._resolve_n_points(parsed, req.n_points)
        budget_min = self._resolve_budget(parsed, req.time_budget_minutes)

        candidates = self._find_candidates(req.query, parsed.categories, bbox, n)
        if len(candidates) < 2:
            raise NoCandidatesFound(
                f"only {len(candidates)} candidate(s) match the query; need ≥ 2"
            )

        # Plan the order on the matrix (no shrinking yet — we need a real
        # Valhalla walk time for the budget summary).
        ordered, info = self._plan_order(candidates, n=n, budget_min=budget_min)

        # Final canonical route (with directions shape) from Valhalla. This
        # is the authoritative walk time — we use it for the budget summary,
        # not the matrix estimate.
        locations = [{"lat": p.lat, "lon": p.lon,
                      "type": "break" if i in (0, len(ordered) - 1) else "via"}
                     for i, p in enumerate(ordered)]
        shape, summary = route_through(locations)
        if summary is None:
            raise UpstreamUnavailable("valhalla /route returned no summary")

        walk_s = float(summary.get("time", 0.0))

        # If we still exceed the budget after the planned order, drop middle
        # stops and re-route until we fit (safety net).
        stops_dropped = info.get("stops_dropped", 0)
        if budget_min is not None and req.allow_auto_relax:
            ordered, walk_s, stops_dropped = self._shrink_to_budget(
                ordered, walk_s, budget_min
            )
            locations = [{"lat": p.lat, "lon": p.lon,
                          "type": "break" if i in (0, len(ordered) - 1) else "via"}
                         for i, p in enumerate(ordered)]
            shape, summary = route_through(locations)
            if summary is None:
                raise UpstreamUnavailable("valhalla /route returned no summary")
            walk_s = float(summary.get("time", 0.0))

        budget_info = self._budget_summary(ordered, walk_s, budget_min, stops_dropped)

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
                    blurb=p.blurb, visit_minutes=visit_time_minutes(p.category),
                ) for p in ordered
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
                                lat=r["lat"], lon=r["lon"], blurb=r.get("blurb"))
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
                      blurb=p.blurb, visit_minutes=visit_time_minutes(p.category))
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
        raw = parse_query(req.query, default_n_points=settings.DEFAULT_N_POINTS)
        return ParsedQuery(
            keywords=raw.get("keywords") or [],
            categories=raw.get("categories") or [],
            n_points=raw.get("n_points"),
            time_budget_minutes=raw.get("time_budget_minutes"),
            region_bbox=self._bbox_from_llm(raw.get("region_bbox")),
            source="llm" if raw.get("source") != "fallback" else "fallback",
        )

    @staticmethod
    def _bbox_from_llm(bbox) -> tuple[float, float, float, float] | None:
        """Validate the LLM-extracted bbox is inside the Grodno region.

        The small Qwen model swaps axes; we accept only well-formed boxes.
        """
        if isinstance(bbox, dict):
            try:
                bbox = (float(bbox["south"]), float(bbox["west"]),
                        float(bbox["north"]), float(bbox["east"]))
            except (KeyError, TypeError, ValueError):
                return None
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return None
        try:
            s, w, n, e = (float(x) for x in bbox)
        except (TypeError, ValueError):
            return None
        b = settings.GRODNO_BBOX
        if not (b["south"] - 0.1 <= s <= n <= b["north"] + 0.1 and
                b["west"] - 0.1 <= w <= e <= b["east"] + 0.1):
            return None
        return (s, w, n, e)

    def _resolve_n_points(self, parsed: ParsedQuery, explicit: int | None) -> int:
        """Client wins. If explicit is None, fall back to LLM or default."""
        if explicit is not None:
            return explicit
        if parsed.n_points is not None:
            return max(settings.MIN_N_POINTS,
                       min(int(parsed.n_points), settings.MAX_N_POINTS))
        return settings.DEFAULT_N_POINTS

    def _resolve_budget(self, parsed: ParsedQuery, explicit: int | None) -> int | None:
        if explicit is not None:
            return explicit
        if parsed.time_budget_minutes is not None:
            return max(settings.MIN_BUDGET_MIN,
                       min(int(parsed.time_budget_minutes), settings.MAX_BUDGET_MIN))
        return None

    def _resolve_bbox(self, parsed: ParsedQuery,
                      explicit: tuple[float, float, float, float] | None
                      ) -> tuple[float, float, float, float] | None:
        if explicit is not None:
            return explicit
        return parsed.region_bbox

    def _find_candidates(self, query: str, categories: list[str],
                         bbox: tuple[float, float, float, float] | None,
                         n: int) -> list[Candidate]:
        """Return a relevance-ranked list of candidate places.

        We over-fetch a bit (MAX_N_POINTS * 2 or pool_size, whichever is
        larger) so the planner has a fallback if some candidates turn out
        to be unreachable.
        """
        qvec = self._embed(query)
        db_cats = db_categories(categories)
        pool_limit = max(settings.CANDIDATE_POOL_SIZE, n * 2)

        rows: list[dict] = []
        if db_cats:
            rows = candidates_by_embedding(
                self.db, qvec, limit=pool_limit,
                region_bbox=list(bbox) if bbox else None,
                categories=db_cats,
            )
        # Top up with unfiltered results if we don't have enough yet.
        if len(rows) < n:
            seen = {r["id"] for r in rows}
            extra = candidates_by_embedding(
                self.db, qvec, limit=pool_limit,
                region_bbox=list(bbox) if bbox else None,
            )
            rows.extend(r for r in extra if r["id"] not in seen)

        cands: list[Candidate] = []
        for i, r in enumerate(rows):
            relevance = 1.0 - (i / max(len(rows), 1))
            if categories and r.get("category") in categories:
                relevance += 0.05
            cands.append(Candidate(
                id=r["id"], name=r["name"], category=r.get("category"),
                lat=r["lat"], lon=r["lon"], blurb=r.get("blurb"),
                relevance=relevance,
            ))
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

    def _shrink_to_budget(
        self,
        ordered: list[Candidate],
        walk_s: float,
        budget_min: int,
    ) -> tuple[list[Candidate], float, int]:
        """Drop middle stops and re-route until walk+visit fits the budget.

        Returns (new_ordered, new_walk_seconds, stops_dropped_count).
        Each iteration hits Valhalla, so this is bounded by len(ordered)-2.
        """
        from .valhalla_client import route_through
        target_s = budget_min * 60
        stops_dropped = 0

        while len(ordered) > 2:
            visits_s = sum(visit_time_minutes(c.category) for c in ordered) * 60
            if walk_s + visits_s <= target_s:
                break
            # Drop the second-to-last (the last stop is the destination and
            # the first is the origin — both are user-anchored). If you want
            # smarter "drop the least interesting stop" selection, do it in
            # the optimiser before calling /route.
            ordered.pop(-2)
            stops_dropped += 1
            shape, summary = route_through([
                {"lat": p.lat, "lon": p.lon,
                 "type": "break" if i in (0, len(ordered) - 1) else "via"}
                for i, p in enumerate(ordered)
            ])
            if summary is None:
                break  # Valhalla refused the sub-route; stop trying
            walk_s = float(summary.get("time", 0.0))

        return ordered, walk_s, stops_dropped

    @staticmethod
    def _budget_summary(ordered: list[Candidate], walk_s: float,
                        budget_min: int | None, stops_dropped: int) -> BudgetInfo | None:
        if budget_min is None:
            return None
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

"""Pipeline orchestrator.

Wires together the 10 steps. One instance, reused across requests, with
explicit dependencies (db) for testability.

Steps in order:
  0  preprocess         query → PreprocessedQuery
  1  extract_intent     query → IntentResult
  2  resolve            IntentResult + client params → ResolvedConstraints
  -- embed query         text → vec (OpenRouter; skipped when unavailable)
  3  retrieve           vec + constraints → list[Candidate] (RRF-fused)
  3.5 rerank            candidates → top-K (Jev; skipped when unavailable)
  4  diversity          candidates → top-N (MMR)
  5  cost               candidates + constraints → CostMatrix
  6  optimize           candidates + cost → ordered list (3 modes)
  7  validate           ordered + cost → ValidatedPlan
  8  render             ValidatedPlan → shape + summary (Valhalla /route)
  9  explain            ValidatedPlan + summary → human-readable string

Degraded mode (no OPENROUTER_API_KEY, or OpenRouter unreachable)
    The three OpenRouter steps each degrade on their own and log ONE warning
    naming the reason: intent falls back to a deterministic parse
    (planner/intent.py `fallback_intent`), the vector signal is dropped so
    retrieval runs on keywords + categories alone, and rerank keeps the
    retrieval order.  A route request still returns points; /health keeps
    reporting `llm`/`embedder` as false because no key is held.

Returns: RouteResponse (Pydantic) — what main.py serves over HTTP.
"""

from __future__ import annotations

import logging
import re as _re
import time as _time

import httpx
import psycopg

from .. import constants, jev
from ..errors import (
    NoCandidatesFound,
    NoRoutePossible,
    UpstreamUnavailable,
)
from ..models import (
    BudgetInfo,
    Candidate,
    CostMatrix,
    GenerateReq,
    LatLon,
    ParsedQuery,
    Place,
    ResolvedConstraints,
    RouteChange,
    RouteChanges,
    RouteResponse,
    RouteSummary,
)
from ..search import fetch_points_by_ids, nearby_places
from ..valhalla_client import optimized_route as valhalla_optimized_route
from ..valhalla_client import ping as valhalla_ping
from .cost import (
    compute_cost_matrix,
    drop_unreachable,
    prune_unroutable_stops,
    visit_time_minutes,
)
from .diversity import mmr_select
from .explain import explain as explain_route
from .intent import extract_intent
from .optimize import optimize
from .preprocess import preprocess
from .render import render
from .rerank import rerank as rerank_pool
from .resolve import resolve
from .retrieve import retrieve, _row_to_candidate
from .validate import validate

log = logging.getLogger(__name__)


def _openrouter_embed(texts: list[str]) -> list[list[float]]:
    """Call OpenRouter embeddings API. Returns list of embedding vectors.

    Returns [] when OpenRouter is not usable — no key, or a request that
    times out / 5xx — which is the signal for keyword-only retrieval.  One
    WARNING per call, naming the reason, so a log reader can tell "no key"
    apart from "key but upstream down".
    """
    api_key = jev.api_key()
    if not api_key:
        log.warning("embed: no OPENROUTER_API_KEY — keyword-only retrieval")
        return []

    try:
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
    except httpx.HTTPError as exc:
        # Timeout, connect error, 4xx/5xx — OpenRouter is not answering.
        log.warning("embed: OpenRouter unreachable (%s) — keyword-only retrieval", exc)
        return []
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        # A 200 whose body carries no usable vectors (not JSON, missing
        # "data", a string where an item belongs) is the same situation from
        # the caller's side: no vector, keep going without one.
        log.warning("embed: unusable embeddings response (%s) — keyword-only retrieval", exc)
        return []


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
        # Vague discovery query with no anchor: taking the single top-ranked hit
        # as the anchor can land on an outlier — "достопримечательности
        # Гродненской области" anchored on a fortress ring spread over 20 km,
        # where no walking leg is possible and the optimizer answered 422.
        # Pick the densest cluster instead: the candidate with the most
        # neighbours inside one GEO_FOCUS_KM radius.
        focus_m = constants.GEO_FOCUS_KM * 1000
        anchor = max(
            candidates,
            key=lambda c: (
                sum(1 for o in candidates if _distance_m(o, c) <= focus_m),
                c.rrf_score,
            ),
        )
        dist = lambda c: _distance_m(c, anchor)
    max_m = constants.GEO_FOCUS_KM * 1000

    if origin is not None or anchor_id is not None:
        # A known position (tourist GPS) or a named place anchors the walk:
        # never inflate the radius here.  Doubling used to pull 50 km-away
        # castles into "Мирский замок", and the optimizer then dropped
        # everything but one stop because no leg was walkable.
        keep = [c for c in candidates if c is anchor or dist(c) <= max_m]
        return keep

    # No anchor at all (vague discovery query): stay local around the best
    # candidate. Widening the radius here used to pull stops hundreds of km
    # apart into one walking tour, and the optimizer then gave up with
    # 422 "optimizer could not produce a route with ≥ 2 stops".
    discovery_max_m = constants.GEO_FOCUS_DISCOVERY_MAX_KM * 1000
    while True:
        keep = [
            c for c in candidates
            if c is anchor or dist(c) <= max_m
        ]
        if len(keep) >= 3 or max_m >= discovery_max_m:
            return keep
        max_m = min(max_m * 2, discovery_max_m)


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


def _with_base_points(
    candidates: list[Candidate],
    rows: list[dict],
    excluded: set[int],
) -> tuple[list[Candidate], list[Candidate]]:
    """Keep the stops the user already has through a refinement.

    A refinement ("добавь кофейню и туалет") must ADD to the route: stops that
    were fetched from the previous turn are re-added after every trim, so the
    only way a stop disappears is an explicit request (excluded_ids) or
    Valhalla's own verdict (no road connects it).

    Returns the merged pool and the base candidates themselves.
    """
    base = [_row_to_candidate(r, 0.0) for r in rows if r["id"] not in excluded]
    have = {c.id for c in candidates}
    merged = list(candidates) + [b for b in base if b.id not in have]
    return merged, base


def _nearby_convenience(
    db: psycopg.Connection,
    base: list[Candidate],
    wanted: set[str],
    *,
    radius_m: int = constants.CONVENIENCE_RADIUS_M,
    max_added: int = constants.CONVENIENCE_MAX_ADDED,
) -> list[Candidate]:
    """Convenience stops (coffee, toilet, ...) that sit ON the route.

    A refinement like «добавь кофейню и туалет» is not a new sightseeing quest:
    the tourist wants a coffee within a short detour of the walk they already
    have. So these are picked from the neighbourhood of the existing stops, not
    from a relevance ranking that happily returns a café 12 km away.
    """
    found: dict[int, Candidate] = {}
    per_stop: dict[int, int] = {}
    for stop in base:
        if per_stop.get(stop.id, 0) >= 2:
            continue
        rows = nearby_places(
            db, stop.lat, stop.lon, radius_km=radius_m / 1000.0, limit=8
        )
        for row in rows:
            cat = (row.get("category") or "").strip().lower()
            if cat not in wanted or row["id"] in found:
                continue
            if row["id"] in {c.id for c in base}:
                continue
            found[row["id"]] = _row_to_candidate(row, 0.0)
            per_stop[stop.id] = per_stop.get(stop.id, 0) + 1
            if len(found) >= max_added:
                return list(found.values())
    return list(found.values())


def _cap_for_valhalla(
    candidates: list[Candidate],
    base: list[Candidate],
    limit: int = constants.VALHALLA_MAX_LOCATIONS,
) -> list[Candidate]:
    """Keep the ordering request inside Valhalla's location limit.

    Valhalla answers /optimized_route with error 150 above 20 locations, which
    the pipeline used to surface as «could not produce a route». Base stops come
    first (they are what the user asked to keep), the rest in relevance order.
    """
    if len(candidates) <= limit:
        return candidates
    base_ids = {c.id for c in base}
    ordered = [c for c in candidates if c.id in base_ids]
    rest = [c for c in candidates if c.id not in base_ids]
    rest.sort(key=lambda c: (c.rerank_score or 0.0, c.relevance), reverse=True)
    return (ordered + rest)[:limit]


def _context_changes(base: list[Candidate], route: list[Candidate]) -> RouteChanges:
    """What a refinement did to the previous route — added / dropped / kept."""
    base_ids = {c.id for c in base}
    route_ids = {c.id for c in route}
    return RouteChanges(
        added=[
            RouteChange(id=c.id, name=c.name)
            for c in route
            if c.id not in base_ids
        ],
        removed=[
            RouteChange(
                id=c.id,
                name=c.name,
                reason="не связано дорогами или не уложилось в лимит",
            )
            for c in base
            if c.id not in route_ids
        ],
        kept=len(base_ids & route_ids),
    )


def _drop_excluded(candidates: list[Candidate], excluded_ids: set[int]) -> list[Candidate]:
    """Drop candidates the user removed by hand.

    A refinement turn carries the stops the user deleted; without this the same
    POI returns on every rebuild and the deletion looks ignored.
    """
    return [c for c in candidates if c.id not in excluded_ids]


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


def _build_cost(
    candidates: list[Candidate], constraints: ResolvedConstraints, costing: str
) -> tuple[list[Candidate], CostMatrix]:
    """Cost matrix, aligned to the candidates, minus what Valhalla cannot reach.

    The matrix pre-filter may drop rows, so the candidate list is re-aligned to
    it; then candidates with no reachable partner at all (a fortress POI with no
    pedestrian edges nearby makes every order look impossible) are dropped.
    """
    cost = compute_cost_matrix(candidates, constraints, costing=costing)
    if cost.indices != list(range(len(candidates))):
        candidates = [candidates[i] for i in cost.indices]
    candidates, cost = drop_unreachable(candidates, cost)
    if len(candidates) < 2:
        raise NoRoutePossible(
            "Valhalla не нашла дороги между нашими точками — "
            "уточните город или район"
        )
    return candidates, cost


def _valhalla_order(
    route: list[Candidate], info: dict, *, costing: str
) -> tuple[list[Candidate], dict]:
    """Let Valhalla order the walk when nothing pins the sequence.

    With no GPS start and no must-visit stops, /optimized_route is the router's
    own solver, so the order the tourist sees is the one Valhalla built. With a
    fixed start or must-visit stops the planned order wins and Valhalla only
    draws it.
    """
    if len(route) < 3:
        return route, info
    coords = [{"lat": c.lat, "lon": c.lon} for c in route]
    try:
        v_order, _, _ = valhalla_optimized_route(coords, costing=costing)
    except UpstreamUnavailable:
        return route, info
    if len(v_order) != len(route) or sorted(v_order) != list(range(len(route))):
        return route, info
    planned = list(info.get("order") or range(len(route)))
    return (
        [route[i] for i in v_order],
        {
            **info,
            "order": [planned[i] for i in v_order],
            "algorithm": f"{info.get('algorithm')}+valhalla",
        },
    )


def _prune_unroutable(
    route: list[Candidate], candidates: list[Candidate], cost: CostMatrix
) -> list[Candidate]:
    """Drop stops the matrix cannot connect to their predecessor in this order.

    Valhalla's own verdict (UNREACHABLE_S = 400 "No path could be found for
    input"): a chapel on a road island, reachable from its neighbour but from
    nothing else, made /route fail for the WHOLE tour — the UI showed every
    point drawn with no line between them.
    """
    route, pruned = prune_unroutable_stops(route, candidates, cost)
    if pruned:
        log.warning(
            "pruned %d stop(s) Valhalla cannot reach in this order: %s",
            len(pruned),
            ", ".join(c.name for c in pruned),
        )
    if len(route) < 2:
        raise NoRoutePossible(
            "Valhalla не нашла дороги между нашими точками — "
            "уточните город или район"
        )
    return route


def _render_tour(
    route: list[Candidate], *, costing: str, origin: LatLon | None
) -> tuple[dict, dict]:
    """Draw the tour, never failing the request over geometry.

    render() already falls back to per-leg geometry; if even that yields
    nothing we answer with the stops and no line, which the UI can explain,
    instead of a 500 for a tour that was planned fine.
    """
    try:
        shape, summary, status = render(route, costing=costing, origin=origin)
        # Log status for monitoring, but don't fail the request
        if status != "usable":
            log.info("render returned status: %s", status)
        return shape, summary
    except UpstreamUnavailable as exc:
        log.warning("render failed (%s) — answering without geometry", exc)
        return {}, {}


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

        # The scope comes from the intent model (typed), not from keyword
        # matching: a region-wide request ("все костёлы Гродненской области") is a
        # drivable list across the voblast, not a walk in one town.
        region_scope = intent.decision.search_scope == "region"

        # 2. Resolve
        constraints = resolve(
            intent,
            explicit_time_budget=req.time_budget_minutes,
            explicit_bbox=req.region_bbox,
            db=self.db,
        )

        # Embed query (OpenRouter). Without an API key — or when the call
        # fails — the pipeline degrades gracefully: retrieval falls back to
        # the keyword signal only and the route is still built.
        vecs = _openrouter_embed([req.query])
        qvec: list[float] = vecs[0] if vecs else []

        # Anchor point for locality: the tourist's GPS start, else the row behind
        # the named town (area_anchor), else the must-visit POI.  Retrieval uses
        # it so the pool really contains places around that point.
        geo_anchor = constraints.area_anchor or (
            constraints.must_visit_ids[0] if constraints.must_visit_ids else None
        )
        near: tuple[float, float] | None = None
        if req.origin is not None:
            near = (req.origin.lat, req.origin.lon)
        elif geo_anchor is not None and not region_scope:
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

        # 3.5 Rerank (Jev) — skipped in keyword mode; rerank() decides and
        # returns the pool in retrieval order when OpenRouter cannot answer.
        candidates = rerank_pool(req.query, candidates, top_k=constants.RERANK_POOL_SIZE)

        # 3.55 Physical duplicates: the same POI exists twice when the curated
        # row and the OSM row disagree on the name ("Новый замок (дворец
        # Стефана Батория)" vs "Новый замок").  Without this the route visits
        # one castle twice under two names.  Candidates are relevance-ordered
        # here, so the best-ranked row of each cluster survives.
        candidates = _drop_duplicates(candidates, constants.DUPLICATE_RADIUS_M)

        # 3.57 A refinement must respect the user's own deletions: a stop removed
        # by hand ("убери форт") may not come back just because it still matches
        # the query. Applied to the pool only — pinned base points are handled
        # separately, they are never dropped silently.
        excluded = set(req.context.excluded_ids) if req.context else set()
        if excluded:
            before = len(candidates)
            candidates = _drop_excluded(candidates, excluded)
            log.info("context: dropped %d excluded stop(s)", before - len(candidates))

        # 3.6 Geographic focus: keep the route walkable — candidates beyond
        # GEO_FOCUS_KM from the tourist's position (or the top-scored hit when
        # position is unknown) are dropped (radius doubles if that leaves <3).
        # Use area_anchor (town/district geo anchor) when available — this keeps
        # the geo focus on the correct town without forcing an arbitrary POI as must-visit.
        # Fall back to must_visit_ids only when there is no separate area anchor.
        if region_scope:
            # "все костёлы Гродненской области" asks for the region, not for one
            # town-sized walking cluster: focusing on the anchor town here is what
            # used to return Grodno-city churches only. Keep the regional spread;
            # Valhalla still builds the walk over whatever stops get selected.
            log.info("geo_focus skipped: region scope in query")
        else:
            geo_anchor = constraints.area_anchor or (
                constraints.must_visit_ids[0] if constraints.must_visit_ids else None
            )
            candidates = _geo_focus(candidates, origin=req.origin, anchor_id=geo_anchor)
        if len(candidates) < 2:
            raise NoCandidatesFound(
                "все кандидаты слишком далеко друг от друга — уточните город или район"
            )

        # 4. MMR diversity — this is a *curation* step: it trims the candidate set
        # to something that fits a walk. It belongs in the plan only when the
        # user named a time budget. Without one they asked for everything ("все
        # костёлы Гродненской области") and get every survivor, honestly long:
        # a multi-day trip beats a silently trimmed dozen.
        if constraints.time_budget_minutes:
            candidates = mmr_select(
                candidates,
                n=min(constants.MMR_POOL_SIZE, len(candidates)),
                db=self.db,
                constraints=constraints,
            )
        else:
            log.info("no time budget: skipping diversity trim, %d candidates", len(candidates))
        if len(candidates) < 2:
            raise NoCandidatesFound(
                f"only {len(candidates)} candidate(s) survived diversity filter"
            )

        # 4b. Refinement: the route the user already had is the starting point,
        # so its stops go back into the pool after retrieval, geo focus and the
        # diversity trim. Without this «добавь кофейню» quietly threw away the
        # museums the user had just built.
        base_candidates: list[Candidate] = []
        if req.context and req.context.base_points:
            base_ids = [p.id for p in req.context.base_points if p.id is not None]
            base_rows = fetch_points_by_ids(self.db, base_ids) if base_ids else []

            # A stop the tourist placed by hand (map click) carries no DB id.
            # Match it to the nearest place so a refinement keeps it instead of
            # quietly dropping what the user put there themselves.
            manual = [
                p
                for p in req.context.base_points
                if p.id is None and (p.pinned or p.source == "user")
            ]
            for point in manual:
                near_rows = nearby_places(
                    self.db, point.lat, point.lon, radius_km=0.06, limit=1
                )
                for row in near_rows:
                    if row["id"] not in {r["id"] for r in base_rows}:
                        base_rows.append(row)
                        log.info(
                            "context: hand-placed stop «%s» matched to «%s»",
                            point.name, row["name"],
                        )

            if base_rows:
                candidates, base_candidates = _with_base_points(
                    candidates, base_rows, excluded
                )
                log.info(
                    "context: %d base stop(s) from the previous route kept, %d new candidate(s) total",
                    len(base_candidates), len(candidates),
                )

                # Convenience categories are chosen by proximity to the route:
                # drop the motorway-side cafés retrieval scored highly but that
                # sit kilometres away, and take the ones by the existing stops.
                wanted = {
                    c for c in constraints.optional_categories
                    if c in constants.CONVENIENCE_CATEGORIES
                }
                if wanted:
                    near = _nearby_convenience(self.db, base_candidates, wanted)
                    near_ids = {c.id for c in near}
                    base_id_set = {b.id for b in base_candidates}
                    # Keep the stops of the current route, the convenience stops
                    # that sit by it, and everything that is not itself a
                    # convenience category (the museums stay, far cafés go).
                    candidates = [
                        c
                        for c in candidates
                        if c.id in base_id_set
                        or c.id in near_ids
                        or (c.category or "").strip().lower() not in wanted
                    ]
                    have = {c.id for c in candidates}
                    candidates += [c for c in near if c.id not in have]
                    log.info(
                        "context: %d convenience stop(s) by the route for %s, %d candidate(s)",
                        len(near), sorted(wanted), len(candidates),
                    )

                candidates = _cap_for_valhalla(candidates, base_candidates)
                log.info("context: pool capped for Valhalla at %d candidate(s)", len(candidates))

        # Transport mode from the request (webapp profile picker); the
        # cost matrix AND the rendered shape must use the same costing.
        # A region-wide request is a drive across the voblast — walking it is
        # impossible, which is exactly why it used to end in 422.
        costing = req.profile or ("auto" if region_scope else "pedestrian")

        # 5. Cost matrix + drop candidates Valhalla cannot connect to anything.
        candidates, cost = _build_cost(candidates, constraints, costing)

        # 6. Optimize. With a known tourist position the route must START there:
        # the optimizer scores orders over the candidate matrix; we prepend the
        # origin afterwards as a fixed first leg (render walks origin → first stop).
        route, info = optimize(candidates, cost, constraints, costing=costing)
        if len(route) < 2:
            raise NoRoutePossible("optimizer could not produce a route with ≥ 2 stops")

        # 6b. Valhalla orders the walk when nothing pins the sequence.
        route, info = _valhalla_order(route, info, costing=costing)

        # 6c. Drop stops the matrix cannot connect to their predecessor in this
        # order (Valhalla's own verdict — see _prune_unroutable).
        route = _prune_unroutable(route, candidates, cost)

        # 7. Validate
        plan = validate(route, cost, constraints, info)

        # 8. Render (Valhalla /route) — origin is the tourist's GPS start
        shape, summary = _render_tour(plan.route, costing=costing, origin=req.origin)

        walk_s = float(summary.get("time", 0.0)) if summary else 0.0
        length_km = summary.get("length") if summary else None

        if walk_s == 0.0 and plan.walk_seconds > 0:
            walk_s = plan.walk_seconds

        # 9. Explain
        explanation = explain_route(plan.route, plan.trace, walk_s, costing)

        ms = int((_time.perf_counter() - t0) * 1000)
        log.info(
            "pipeline.ok query_len=%d ms=%d n_stops=%d walk_s=%.0f budget_min=%s source=%s",
            len(req.query), ms, len(plan.route), walk_s,
            constraints.time_budget_minutes, intent.source,
        )

        changes = (
            _context_changes(base_candidates, plan.route) if base_candidates else None
        )

        return self._build_response(
            intent=intent,
            changes=changes,
            constraints=constraints,
            plan=plan,
            shape=shape,
            walk_s=walk_s,
            length_km=length_km,
            explanation=explanation,
            costing=costing,
        )

    def reroute(self, point_ids: list[int], profile: str | None = None) -> RouteResponse:
        """Re-route a chosen list of place IDs."""
        from ..search import fetch_points_by_ids
        rows = fetch_points_by_ids(self.db, point_ids)
        if len(rows) != len(point_ids):
            missing = set(point_ids) - {r["id"] for r in rows}
            raise NoCandidatesFound(f"unknown point_ids: {sorted(missing)}")

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
        route, info = optimize(candidates, cost, constraints, costing=profile or "pedestrian")
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
            changes=None,
            shape=shape,
            summary=RouteSummary(length_km=length_km, time_seconds=walk_s),
            costing=profile or "pedestrian",
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

        openrouter_ok = bool(jev.api_key())

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
        changes: RouteChanges | None,
        shape: dict,
        walk_s: float,
        length_km: float | None,
        explanation: str,
        costing: str = "pedestrian",
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

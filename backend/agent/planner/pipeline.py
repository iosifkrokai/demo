"""Pipeline orchestrator.

Wires together the 10 steps. One instance, reused across requests, with
explicit dependencies (db) for testability.

Steps in order:
  0  preprocess         query → PreprocessedQuery
  1  interpret          query + explicit UI filters → TripRequirements
                        (PydanticAI agent over OpenRouter; deterministic
                         fallback when no key/model), then →
                        IntentResult for resolve()
  2  resolve            IntentResult + client params → ResolvedConstraints
  -- embed query         text → vec (OpenRouter; skipped when unavailable)
  3  retrieve           vec + constraints → list[Candidate] (RRF-fused)
  4  diversity          candidates → top-N (MMR)
  5  cost               candidates + constraints → CostMatrix
  6  optimize           candidates + cost → ordered list (3 modes)
  7  validate           ordered + cost → ValidatedPlan
  8  render             ValidatedPlan → shape + summary (Valhalla /route)
  9  explain            ValidatedPlan + summary → human-readable string
  9b verify             TripRequirements + final route/geometry → per-requirement
                        satisfaction (planner/verify.py — the deterministic
                        verifier, NOT the model)

Degraded mode (no OPENROUTER_API_KEY, or the agent/OpenRouter unreachable)
    The interpretation step degrades to the deterministic parse in
    planner/intent.py (`build_requirements` → `_deterministic_requirements`)
    while keeping every explicit UI filter; the vector signal is dropped so
    retrieval runs on keywords + categories alone.  A route request still
    returns points; /health keeps reporting `llm`/`embedder` as false because
    no key is held.

Returns: RouteResponse (Pydantic) — what main.py serves over HTTP.  Its
`status`/`requirements` fields are the verifier's verdict, not the model's.
"""

from __future__ import annotations

import logging
import re as _re
import time as _time

import httpx
import psycopg

from .. import constants, progress, taxonomy
from ..config import openrouter_api_key
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
    Interpretation,
    LatLon,
    ParsedQuery,
    Place,
    RequirementSignal,
    ResolvedConstraints,
    RouteChange,
    RouteChanges,
    RouteResponse,
    RouteSummary,
)
from ..search import fetch_points_by_ids, nearby_places, _name_match_search
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
from . import interpret_cache
from .intent import (
    build_requirements,
    intent_from_requirements,
    mark_out_of_coverage,
)
from .optimize import optimize
from .preprocess import preprocess
from .refine import (
    interpret_refinement,
    is_excluded_category,
    reason_text,
    reorder_stops,
    visit_minutes_of,
)
from .render import render
from .resolve import resolve
from .retrieve import apply_negative_filter, retrieve, _row_to_candidate
from .validate import validate
from .verify import overall_status, verify

log = logging.getLogger(__name__)


def _openrouter_embed(texts: list[str]) -> list[list[float]]:
    """Call OpenRouter embeddings API. Returns list of embedding vectors.

    Returns [] when OpenRouter is not usable — no key, or a request that
    times out / 5xx — which is the signal for keyword-only retrieval.  One
    WARNING per call, naming the reason, so a log reader can tell "no key"
    apart from "key but upstream down".
    """
    api_key = openrouter_api_key()
    if not api_key:
        log.warning("embed: no OPENROUTER_API_KEY — keyword-only retrieval")
        return []

    # An embedding is a pure function of the text and the model, so it is the
    # safest thing here to remember: no verdict, no measurement, nothing that
    # can go stale about the world.
    cache_key = interpret_cache.embed_key(texts, constants.EMBED_MODEL)
    cached = interpret_cache.EMBED_CACHE.get(cache_key)
    if cached is not None:
        return cached

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
            vectors = [item["embedding"] for item in body["data"]]
            interpret_cache.EMBED_CACHE.put(cache_key, vectors)
            return vectors
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
    rest.sort(key=lambda c: c.relevance, reverse=True)
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


def _synthetic_cost(
    candidates: list[Candidate], costing: str = "pedestrian"
) -> CostMatrix:
    """A straight-line cost matrix for when Valhalla cannot answer.

    A refinement must still return the route the user has: if the road matrix
    is unavailable we fall back to great-circle legs and the taxonomy's visit
    estimates, instead of turning the request into a 422/503.
    """
    speed_ms = 1.3 if costing == "pedestrian" else 8.0
    n = len(candidates)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                matrix[i][j] = _distance_m(candidates[i], candidates[j]) / speed_ms
    return CostMatrix(
        walk_seconds=matrix,
        visit_minutes=[visit_minutes_of(c) for c in candidates],
        indices=list(range(n)),
    )


def _refinement_cost(
    route: list[Candidate], constraints: ResolvedConstraints, costing: str
) -> tuple[list[Candidate], CostMatrix]:
    """Best-effort cost matrix for a refinement route.

    Never raises and never drops a stop the user kept: if the road matrix
    pre-filter would remove one of them, fall back to straight-line legs so the
    base points survive into the refinement (the 422 this contract fixes).
    """
    base_ids = {c.id for c in route}
    try:
        cost = compute_cost_matrix(route, constraints, costing=costing)
    except (UpstreamUnavailable, NoRoutePossible):
        return route, _synthetic_cost(route, costing)

    if cost.indices != list(range(len(route))):
        aligned = [route[i] for i in cost.indices]
        if not base_ids <= {c.id for c in aligned}:
            return route, _synthetic_cost(route, costing)
        route = aligned
        cost = CostMatrix(
            walk_seconds=cost.walk_seconds,
            visit_minutes=cost.visit_minutes,
            indices=list(range(len(route))),
        )
    return route, cost


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


def _is_service_code(code: str) -> bool:
    """True when a taxonomy code is a service (a café, a toilet, a hotel)."""
    try:
        return taxonomy.role(code) == "service"
    except Exception:  # unknown/unmapped code → treat it as a destination
        return False


def _is_sight_stop(candidate: Any) -> bool:
    """True when a candidate is a destination, not a service the user asked for.

    A service POI (toilet/café/hotel) can be *on* the walk; it must never be the
    thing the walk is built around.  Unknown category codes count as sights —
    the taxonomy is the only authority, and it only ever gains codes.
    """
    try:
        return taxonomy.role(candidate.category) != "service"
    except Exception:  # unknown/unmapped code → treat it as a destination
        return True


def _interpretation(
    requirements: Any | None, status: str | None
) -> Interpretation | None:
    """What the system understood, in one compact block for the client.

    Codes and numbers only — the client localises ``code``/``reason`` itself.
    ``unmet`` lists EVERY requirement that the verifier did not prove satisfied
    (unmet, uncertain or still pending), so a request whose mandatory stop could
    not be placed is reported explicitly instead of quietly returning a plan
    that ignores it.
    """
    if requirements is None:
        return None
    # Per-requirement provenance defaults to wherever the reading came from;
    # a requirement the user set with a visible control keeps "ui".
    origin = "agent" if requirements.source in ("llm", "mixed") else "fallback"

    def signal(r: Any) -> RequirementSignal:
        return RequirementSignal(
            kind=r.kind,
            strength=r.strength,
            code=r.code,
            name=r.name,
            origin="ui" if r.source == "ui" else origin,
            status=r.status,
            reason=r.reason,
            place_ids=list(r.place_ids),
        )

    signals = [signal(r) for r in requirements.requirements]
    return Interpretation(
        source=requirements.source,
        locale=requirements.locale,
        status=status or "pending",
        adults=requirements.party.adults,
        children=requirements.party.children,
        children_ages=list(requirements.party.children_ages),
        mobility=list(requirements.party.mobility),
        budget_minutes=requirements.budget_minutes,
        areas=list(requirements.areas),
        transport=requirements.costing,
        result_mode=requirements.result_mode,
        round_trip=requirements.round_trip,
        requirements=signals,
        unmet=[s for s in signals if s.status != "satisfied"],
        unknowns=list(requirements.unknowns),
    )


def _outside_left_unresolved(
    requirements: Any, constraints: ResolvedConstraints
) -> list[str]:
    """Names the reading placed outside the region that stayed unresolvable.

    The cross-check that keeps a wrong reading harmless: if the name DID resolve
    to a place inside the region — the model flagged «Старый замок» although it
    is in Grodno — it is not a refusal, whatever the reading said. Only a name
    that has no place here at all (Vilnius Cathedral) is left in the list.
    """
    flagged = getattr(requirements, "outside_coverage", None) or []
    if not flagged:
        return []
    resolved = {
        (n or "").strip().lower()
        for n in (getattr(constraints, "resolved_names", None) or [])
    }
    return [n for n in flagged if (n or "").strip().lower() not in resolved]


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



def _services_along_evidence(
    db: Any, requirements: Any, shape: Any
) -> Any:
    """Measure the services beside the line for the codes the requirements name.

    The verifier decides what a requirement means; it owns no database, so this
    is where the geometry is actually measured (``agent.services``). Returns

    * ``None`` — nothing to measure (no service/interest codes, or no usable
      line): the older semantics stay, so an absent café is still honestly
      ``unmet``;
    * ``ServiceAlongEvidence(measured=True, by_code=...)`` — measured. A code
      missing from the mapping means «рядом нет», which is evidence;
    * ``ServiceAlongEvidence(measured=False, ...)`` — the measurement itself
      failed. A broken query is not evidence that the café is absent, so the
      verifier reports ``uncertain`` rather than ``unmet``.
    """
    codes = sorted(
        {
            r.code
            for r in getattr(requirements, "requirements", [])
            if r.kind in ("service", "interest") and r.code
        }
    )
    if not codes or not shape:
        return None
    from .. import services as services_mod
    from .verify import ServiceAlongEvidence

    try:
        answer = services_mod.services_along(db, shape, categories=codes, limit=services_mod.MAX_SERVICES * 2)
    except Exception:  # measurement is best-effort; its failure is reported, not hidden
        log.warning("services_along: measurement failed", exc_info=True)
        return ServiceAlongEvidence(measured=False, by_code={})
    out: dict[str, list[dict]] = {}
    for item in answer.get("items", []):
        out.setdefault(str(item.get("category")), []).append(item)
    return ServiceAlongEvidence(measured=True, by_code=out)



class Pipeline:
    """Stateless planner. One instance, reused across requests."""

    def __init__(self, db: psycopg.Connection):
        self.db = db

    @staticmethod
    def _left(t0: float) -> float:
        """Seconds left of this request's end-to-end deadline (may be < 0)."""
        return constants.REQUEST_DEADLINE_S - (_time.perf_counter() - t0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, req: GenerateReq) -> RouteResponse:
        t0 = _time.perf_counter()
        deadline_trim: int | None = None
        deadline_order_skipped = False
        deadline_geometry_skipped = False

        # A refinement turn is not a new plan: the route the user already has is
        # the input.  Handled before retrieval so a delta instruction can never
        # be turned into a fresh region-wide route — and so the base points
        # cannot be lost on the way (the 422 this contract fixes).
        if req.context and req.context.base_points:
            base = self._refinement_base(req.context)
            if base:
                return self._generate_refinement(req, base, t0)

        # 0. Preprocess
        pre = preprocess(req.query)

        # 1. Interpretation — the plan is built from `TripRequirements`.  The
        # tool-using agent (planner/agent_interpret.py) fills the contract when
        # it can; the deterministic parse answers otherwise (no key, no SDK, a
        # model/tool failure).  `intent` is derived FROM that contract, so
        # resolve() grounds the reading that actually produced the plan and not
        # a second, keyword-only guess.
        # The client cannot see inside this request, so the pipeline says where
        # it got to. Codes only; the client localises them.
        progress.note(progress.STAGE_INTERPRETING)
        requirements = build_requirements(
            req.query, req, db=self.db, wall_clock_s=self._left(t0)
        )
        intent = intent_from_requirements(requirements, req.query)

        # The scope comes from the intent model (typed), not from keyword
        # matching: a region-wide request ("все костёлы Гродненской области") is a
        # drivable list across the voblast, not a walk in one town.
        region_scope = intent.decision.search_scope == "region"

        # 2. Resolve
        constraints = resolve(
            intent,
            explicit_time_budget=req.time_budget_minutes,
            explicit_bbox=req.region_bbox,
            outside=requirements.outside_coverage,
            db=self.db,
        )

        # 2b. Coverage gate. The reading can say that a name in the request lies
        # outside the region this system serves; what follows from that is
        # decided here, before anything is retrieved. Without this gate a request
        # about Vilnius Cathedral was answered `ready` with four stops in Лида:
        # the name matcher had accepted a different cathedral in a different town
        # as the named place, and the route was planned around it. A refusal is
        # the honest answer; a route to somewhere else is not.
        outside_left = _outside_left_unresolved(requirements, constraints)
        if outside_left:
            return self._refuse_out_of_coverage(
                req, requirements, intent, constraints, outside_left, t0
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
        progress.note(progress.STAGE_SEARCHING)
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

        # 3.5 Rerank: retired with Jev.  `retrieve()` already fuses the vector
        # and keyword signals (RRF) into a relevance order, and the ordered
        # pool is what the pipeline uses; the Jev scoring call is gone with the
        # rest of Jev (spec §4.2 keeps Jev only if it measurably pays off).

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

        # 4b. Refinement turns are handled before retrieval (see the top of
        # generate()).  Reaching here means either a first turn (no context) or
        # a context whose base points could not be resolved — in both cases
        # there is no previous route to preserve.
        base_candidates: list[Candidate] = []

        # Transport mode from the request (webapp profile picker); the
        # cost matrix AND the rendered shape must use the same costing.
        # A region-wide request is a drive across the voblast — walking it is
        # impossible, which is exactly why it used to end in 422.
        costing = req.profile or ("auto" if region_scope else "pedestrian")

        # 5. Cost matrix + drop candidates Valhalla cannot connect to anything.
        # A wide query (whole oblast, no time budget) puts up to 50 stops into a
        # 50×50 Valhalla matrix — a fixed ~20 s.  When the request deadline is
        # already close, plan a smaller tour instead of running out of time: the
        # trim is reported in `debug.deadline` and the verifier still judges the
        # result honestly.
        left = self._left(t0)
        if left < constants.COST_MATRIX_MIN_LEFT_S and len(candidates) > constants.POOL_TRIM_SIZE:
            log.info(
                "deadline: %.1fs left — trimming %d candidates to %d before the cost matrix",
                left, len(candidates), constants.POOL_TRIM_SIZE,
            )
            candidates = sorted(candidates, key=lambda c: c.relevance, reverse=True)
            candidates = candidates[: constants.POOL_TRIM_SIZE]
            deadline_trim = constants.POOL_TRIM_SIZE

        # A service is *on* the walk, never what the walk is built around: the
        # stop pool is the sights, and cafés/toilets/hotels stay on the line
        # (the along-the-route hints).  This was previously applied only in the
        # retry below, i.e. only once the order had already collapsed to one
        # stop — so a two-stop pool kept a café as stop #1 and the walk was built
        # around a place the user never asked to visit.
        progress.note(progress.STAGE_SELECTING)
        all_candidates = list(candidates)
        sights = [c for c in candidates if _is_sight_stop(c)]

        # Too few sights to walk between is usually the *query's own words*
        # being narrow, not the town being empty: «вечерняя прогулка по
        # Советской» retrieves mostly what is *named* Sovetskaya — the street,
        # the museum on it, a café, a theatre.  Dropping the services then leaves
        # too little, and the optimizer either promotes a café back to a stop or
        # refuses the request with a 422 about stops.  Ask again without the
        # words: the position, the prohibitions, the interests and the region
        # stay, which is what «a walk along this street» actually needs.
        if len(sights) < 3 and not region_scope:
            # The categories are relaxed too, and the *service* ones dropped:
            # «старый Гродно, туалет обязателен, кафе если по пути» steers
            # retrieval with two service signals, and the whole pool came back
            # cafés and toilets — zero sights — so the walk had nothing to be
            # built from.  A service is never a stop (see the pool rule below),
            # and the services layer finds them along the line anyway; what the
            # second pass needs is the ordinary places of the area.
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
            wider = retrieve(relaxed, qvec, self.db, query_text="", near=near)
            if relaxed.forbidden_categories or relaxed.forbidden_keywords:
                wider = apply_negative_filter(wider, relaxed)
            wider = _geo_focus(
                wider,
                origin=req.origin,
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
                candidates = _drop_excluded(candidates + added, excluded)
                sights = [c for c in candidates if _is_sight_stop(c)]

        if 2 <= len(sights) < len(candidates):
            log.info(
                "pool: %d sight(s) of %d candidate(s) — services stay on the line",
                len(sights), len(candidates),
            )
            candidates = sights
        elif len(sights) < 2:
            # Nowhere nearby to walk to even after widening: the café the user
            # named *is* the destination («где поесть» in a thin area), so the
            # pool keeps its services rather than refusing the request.
            log.info("pool: %d sight(s) — the services are the destination here", len(sights))

        progress.note(progress.STAGE_MEASURING_LEGS)
        candidates, cost = _build_cost(candidates, constraints, costing)

        # 6. Optimize. With a known tourist position the route must START there:
        # the optimizer scores orders over the candidate matrix; we prepend the
        # origin afterwards as a fixed first leg (render walks origin → first stop).
        progress.note(progress.STAGE_ORDERING)
        route, info = optimize(candidates, cost, constraints, costing=costing)
        if len(route) < 2 and len(candidates) > 1:
            # The most relevant stop of a service-only query («туалет по пути»)
            # is the service itself.  If that POI sits far from everything else,
            # the walkability cap rejects every insertion, the order collapses to
            # one stop and the request dies as a 422 — which hides a plan that
            # was otherwise fine.  Retry anchored on the SIGHT stops: the walk
            # gets a destination, and the service the user asked for is then
            # reported unmet by the verifier instead of failing the request.
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
        if len(route) < 2 and len(all_candidates) > len(candidates):
            # The sights-only pool could not be walked: nothing connects within
            # the walkable cap.  A thin answer beats a refusal (the request is
            # valid, the area is just sparse) — put the services back and let the
            # verifier report what the walk does and does not contain.
            log.info("optimize: sights alone give %d stop(s) — retrying with services", len(route))
            retry_candidates, retry_cost = _build_cost(all_candidates, constraints, costing)
            retry_route, retry_info = optimize(
                retry_candidates, retry_cost, constraints, costing=costing
            )
            if len(retry_route) >= 2:
                candidates, cost, route, info = (
                    retry_candidates, retry_cost, retry_route, retry_info,
                )
        if len(route) < 2:
            raise NoRoutePossible("optimizer could not produce a route with ≥ 2 stops")

        # 6b. Valhalla orders the walk when nothing pins the sequence.  Its
        # `optimized_route` over a region-wide tour is unbounded (60 s+ measured
        # on «замки Гродненской области»): under deadline pressure the matrix
        # order stands, and the response says so.
        if self._left(t0) >= constants.VALHALLA_ORDER_MIN_LEFT_S:
            route, info = _valhalla_order(route, info, costing=costing)
        else:
            log.info(
                "deadline: %.1fs left — skipping Valhalla re-ordering",
                self._left(t0),
            )
            deadline_order_skipped = True

        # 6c. Drop stops the matrix cannot connect to their predecessor in this
        # order (Valhalla's own verdict — see _prune_unroutable).
        route = _prune_unroutable(route, candidates, cost)

        # 7. Validate
        plan = validate(route, cost, constraints, info)

        # 8. Render (Valhalla /route) — origin is the tourist's GPS start.
        # Skipped when the deadline is spent: the response then carries no
        # geometry, which the verifier reports as `geometry_missing`/`degraded`
        # rather than the client waiting for a hang.
        progress.note(progress.STAGE_DRAWING)
        if self._left(t0) >= constants.RENDER_MIN_LEFT_S:
            shape, summary = _render_tour(plan.route, costing=costing, origin=req.origin)
        else:
            log.info("deadline: %.1fs left — skipping geometry", self._left(t0))
            shape, summary = {}, {}
            deadline_geometry_skipped = True

        walk_s = float(summary.get("time", 0.0)) if summary else 0.0
        length_km = summary.get("length") if summary else None

        if walk_s == 0.0 and plan.walk_seconds > 0:
            walk_s = plan.walk_seconds

        # 9. Explain
        explanation = explain_route(plan.route, plan.trace, walk_s, costing)

        # 9b. Verify — the DETERMINISTIC verifier decides, per requirement,
        # whether the request was actually honoured against the final route and
        # the Valhalla geometry (spec §4.4).  The interpretation model proposed
        # the meaning of the request; it gets no vote here, and an unmet hard
        # requirement is reported as such, never explained away.
        progress.note(progress.STAGE_CHECKING)
        verify(
            requirements,
            plan,
            shape,
            _services_along_evidence(self.db, requirements, shape),
        )
        status = overall_status(requirements)
        progress.note(progress.STAGE_DONE)

        ms = int((_time.perf_counter() - t0) * 1000)
        log.info(
            "pipeline.ok query_len=%d ms=%d n_stops=%d walk_s=%.0f budget_min=%s "
            "source=%s status=%s",
            len(req.query), ms, len(plan.route), walk_s,
            constraints.time_budget_minutes, intent.source, status,
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
            requirements=requirements,
            status=status,
            deadline={
                "budget_s": constants.REQUEST_DEADLINE_S,
                "used_s": round(_time.perf_counter() - t0, 3),
                "pool_trimmed_to": deadline_trim,
                "valhalla_order_skipped": deadline_order_skipped,
                "geometry_skipped": deadline_geometry_skipped,
            },
        )

    def _refuse_out_of_coverage(
        self,
        req: GenerateReq,
        requirements: Any,
        intent: Any,
        constraints: ResolvedConstraints,
        names: list[str],
        t0: float,
    ) -> RouteResponse:
        """Answer "not here" instead of planning a route somewhere else.

        No stop is returned, and the reason is not prose invented for the client:
        the contract carries a hard requirement that nothing in this region can
        satisfy, so the verifier's own rule makes the status `infeasible` and the
        client localises the reason code. Building a plan out of look-alikes would
        look more complete and be worse — it would walk a tourist to another
        town's landmarks under the name they asked for.
        """
        mark_out_of_coverage(requirements, names)
        plan = validate([], None, constraints, {}, requirements=requirements)
        return self._build_response(
            intent=intent,
            changes=None,
            constraints=constraints,
            plan=plan,
            shape={},
            walk_s=0.0,
            length_km=0.0,
            explanation=(
                "Маршрут не построен: "
                + ", ".join(names)
                + " — вне зоны покрытия (Гродненская область)."
            ),
            costing=req.profile,
            requirements=requirements,
            status=overall_status(requirements),
            deadline={
                "budget_s": constants.REQUEST_DEADLINE_S,
                "used_s": round(_time.perf_counter() - t0, 3),
                "pool_trimmed_to": None,
                "valhalla_order_skipped": False,
                "geometry_skipped": False,
            },
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
                    town=p.town, district=p.district, photo=p.photo,
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

        openrouter_ok = bool(openrouter_api_key())

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

    def _refinement_base(self, ctx) -> list[Candidate]:
        """The previous route's stops, as Candidate objects.

        IDs come straight from the DB (stable identity), and a hand-placed
        stop (map click, no DB id) is matched to the nearest place so a
        refinement keeps what the user put there themselves.  Excluded ids are
        filtered by the caller (they are an explicit removal, and the changes
        report still has to name them).
        """
        base_ids = [p.id for p in ctx.base_points if p.id is not None]
        base_rows = fetch_points_by_ids(self.db, base_ids) if base_ids else []

        manual = [
            p
            for p in ctx.base_points
            if p.id is None and (p.pinned or p.source == "user")
        ]
        for point in manual:
            try:
                near_rows = nearby_places(
                    self.db, point.lat, point.lon, radius_km=0.06, limit=1
                )
            except Exception as exc:  # a hand-placed stop is best-effort
                log.warning("context: hand-placed stop lookup failed: %s", exc)
                continue
            for row in near_rows:
                if row["id"] not in {r["id"] for r in base_rows}:
                    base_rows.append(row)
                    log.info(
                        "context: hand-placed stop «%s» matched to «%s»",
                        point.name, row["name"],
                    )

        seen: set[int] = set()
        base: list[Candidate] = []
        for row in base_rows:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            base.append(_row_to_candidate(row, 0.0))
        return base

    def _refinement_removals(
        self,
        directive,
        base: list[Candidate],
        excluded: set[int],
    ) -> set[int]:
        """Stop ids the instruction asks to remove, on top of excluded_ids.

        Applied for every operation: an instruction that both adds and removes
        ("добавь кафе и убери музеи") must still drop the museums.  A named stop
        ("убери форт") is matched against the previous route by normalised name;
        an excluded category ("без музеев") by canonical taxonomy code.
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

        Convenience categories (a café, a toilet) come from the neighbourhood
        of the existing stops, not from a relevance ranking that happily returns
        one 12 km away.  Requested sight categories are looked for a little
        further out, and explicitly named places are resolved in the DB.
        """
        found: dict[int, Candidate] = {}
        base_ids = {c.id for c in base}

        try:
            convenience = {
                c for c in directive.add_categories
                if c in constants.CONVENIENCE_CATEGORIES
            }
            if convenience:
                for c in _nearby_convenience(self.db, base, convenience):
                    found[c.id] = c

            sights = {
                c for c in directive.add_categories
                if c not in constants.CONVENIENCE_CATEGORIES
            }
            if sights:
                for c in _nearby_convenience(
                    self.db, base, sights,
                    radius_m=constants.CONVENIENCE_RADIUS_M * 4,
                    max_added=constants.CONVENIENCE_MAX_ADDED,
                ):
                    found.setdefault(c.id, c)
        except Exception as exc:  # DB/nearby lookup is best-effort
            log.warning("refinement: nearby add lookup failed: %s", exc)

        for name in directive.add_names:
            try:
                rows = _name_match_search(self.db, name, limit=1)
            except Exception as exc:
                log.warning("refinement: named add lookup failed: %s", exc)
                continue
            for row in rows:
                if row["id"] in base_ids or row["id"] in found:
                    continue
                found[row["id"]] = _row_to_candidate(row, 0.0)

        return [c for c in found.values() if c.id not in base_ids]

    def _generate_refinement(
        self, req: GenerateReq, base: list[Candidate], t0: float
    ) -> RouteResponse:
        """Apply ONE refinement operation to the route the user already has.

        The base stops are the route.  They are kept unless the instruction
        explicitly removes/excludes them; the operation either reorders them,
        adds to them, or is refused with a machine reason code and the route is
        returned untouched.  Never 422: the base points always survive.
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
            # none / remove / unsupported: the previous route, minus explicit
            # removals.  An unsupported instruction changes nothing else.
            route = list(kept)
            algorithm = "refinement_keep"

        info: dict = {
            "algorithm": algorithm,
            "order": list(range(len(route))),
            "refinement_operation": directive.operation,
        }

        requirements = build_requirements(instruction or req.query, req, db=self.db)
        intent = intent_from_requirements(requirements, instruction or req.query)
        constraints = resolve(
            intent,
            explicit_time_budget=req.time_budget_minutes,
            explicit_bbox=req.region_bbox,
            db=self.db,
        )
        costing = req.profile or "pedestrian"

        # The cost matrix is best-effort; the base points survive regardless.
        route, cost = _refinement_cost(route, constraints, costing)

        plan = validate(route, cost, constraints, info)
        shape, summary = _render_tour(route, costing=costing, origin=req.origin)
        # The deterministic verifier decides the fate of the (delta) requirements
        # against the refined route — the model never does.
        verify(
            requirements,
            plan,
            shape,
            _services_along_evidence(self.db, requirements, shape),
        )
        status = overall_status(requirements)

        walk_s = float(summary.get("time", 0.0)) if summary else 0.0
        if walk_s == 0.0 and plan.walk_seconds > 0:
            walk_s = plan.walk_seconds
        length_km = summary.get("length") if summary else None

        if directive.operation == "unsupported":
            explanation = directive.detail or reason_text(directive.reason_code or "")
        else:
            explanation = explain_route(route, plan.trace, walk_s, costing)

        changes = _context_changes(base, route)

        ms = int((_time.perf_counter() - t0) * 1000)
        log.info(
            "pipeline.refine op=%s reason=%s ms=%d base=%d route=%d added=%d",
            directive.operation, directive.reason_code, ms,
            len(base), len(route), len(additions),
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
        # Machine-readable refinement outcome; the human text is a fallback for
        # the UI, the reason code is the contract.
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
        status: str | None = None,
        deadline: dict | None = None,
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
                    photo=p.photo,
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
            status=status,
            requirements=(
                requirements.public_requirements() if requirements is not None else None
            ),
            interpretation=_interpretation(requirements, status),
            debug={
                "intent_source": intent.source,
                "intent_latency_ms": intent.latency_ms,
                "deadline": deadline,
                # Whether this answer came from the cache, and how the cache has
                # been doing — a claim about speed that the response can be held to.
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

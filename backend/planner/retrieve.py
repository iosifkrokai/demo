"""Step 3 — Multi-signal candidate retrieval + RRF fusion.

Fuses vector, keyword, must and category signals; output has `rrf_score`.

The signals run against a `PostgresPlaceRepository`, not a connection: this step
reads places and nothing else, so it should not be able to reach past them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from contracts.planner import Candidate, Photo, ResolvedConstraints
from core import constants
from db.models.place import Place
from db.store.mappers import parse_fun_facts, parse_links, photo_of
from db.store.places import PostgresPlaceRepository
from reference.taxonomy import db_values

CATEGORY_KEYWORD_TO_LLM: dict[str, str] = {
    "замок": "замок", "замки": "замок", "замка": "замок", "замкам": "замок", "замках": "замок",
    "замком": "замок", "замку": "замок",
    "костёл": "костёл", "костёлы": "костёл", "костела": "костёл",
    "костелах": "костёл", "костел": "костёл",
    "дворец": "дворец", "дворцы": "дворец", "дворца": "дворец",
    "дворце": "дворец", "дворцов": "дворец",
    "музей": "музей", "музеи": "музей", "музея": "музей",
    "музее": "музей", "музеев": "музей",
    "памятник": "памятник", "памятники": "памятник", "памятника": "памятник",
    "парк": "парк", "парки": "парк", "парка": "парк",
    "парке": "парк", "парков": "парк",
    "монастырь": "монастырь", "монастыри": "монастырь",
    "монастыря": "монастырь",
    "церковь": "церковь", "церкви": "церковь",
    "храм": "храм", "храмы": "храм",
    "усадьба": "усадьба", "усадьбы": "усадьба",
    "архитектура": "архитектура", "архитектурный": "архитектура",
    "инфраструктура": "инфраструктура",
    "крепость": "замок",
}


def _detect_category_keywords(query: str) -> list[str]:
    """Extract LLM-category labels from query text.

    Explicit category words must still steer retrieval even for "vague" queries.
    """
    words = re.findall(r"[а-яё]{3,}", query.lower())
    cats: list[str] = []
    seen: set[str] = set()
    for w in words:
        llm_cat = CATEGORY_KEYWORD_TO_LLM.get(w)
        if llm_cat and llm_cat not in seen:
            seen.add(llm_cat)
            cats.append(llm_cat)
    return cats


def _nearby_signal(
    places: PostgresPlaceRepository,
    lat: float,
    lon: float,
    limit: int,
) -> list[tuple[int, float]]:
    """Places around a known point (tourist GPS or a named town)."""
    rows = places.nearby(lat, lon, radius_km=constants.GEO_FOCUS_KM, limit=limit)
    return [(p.id, 0.0) for p in rows if p.id is not None]


def retrieve(
    constraints: ResolvedConstraints,
    query_embedding: list[float],
    places: PostgresPlaceRepository,
    query_text: str = "",
    near: tuple[float, float] | None = None,
) -> list[Candidate]:
    """Multi-signal → RRF → negative-filter → hydrate Candidate rows.

    Returns up to RETRIEVAL_POOL_SIZE candidates, ordered by rrf_score DESC.
    """
    pool_limit = constants.RETRIEVAL_POOL_SIZE

    explicit_cat_kw = _detect_category_keywords(query_text)
    all_cats: list[str] = list({
        *(constraints.optional_categories or []),
        *explicit_cat_kw,
    })

    vector_signal: list[tuple[int, float]] = []
    if query_embedding:
        vector_signal = _vector_signal(query_embedding, places, constraints, limit=pool_limit)

    kw_query = " ".join(constraints.must_visit_keywords + [query_text]).strip()
    keyword_signal = _keyword_signal(places, kw_query, limit=pool_limit // 2) if kw_query else []

    must_signal = [(pid, 0.0) for pid in constraints.must_visit_ids]

    cat_signal: list[tuple[int, float]] = []
    if all_cats:
        cat_signal = _category_signal(places, constraints, all_cats, limit=pool_limit // 2)

    cat_first_signal: list[tuple[int, float]] = []
    if explicit_cat_kw:
        primary_cat = [explicit_cat_kw[0]]
        cat_first_signal = _category_signal(
            places, constraints, primary_cat, limit=pool_limit
        )

    near_signal: list[tuple[int, float]] = []
    if near is not None:
        near_signal = _nearby_signal(places, near[0], near[1], limit=pool_limit)

    fused = rrf_fuse(
        [vector_signal, keyword_signal, must_signal, cat_signal, cat_first_signal,
         near_signal],
        k=constants.RRF_K,
    )

    top_ids = sorted(fused, key=lambda pid: -fused[pid])[:pool_limit]

    candidates = _hydrate(places, top_ids, fused)

    if constants.NEGATIVE_FILTER_ENABLED:
        candidates = apply_negative_filter(candidates, constraints)

    if constraints.must_visit_ids:
        present_ids = {c.id for c in candidates}
        missing = [mid for mid in constraints.must_visit_ids if mid not in present_ids]
        if missing:
            for place in places.get_by_ids(missing):
                candidates.append(candidate_of(place, fused.get(place.id or 0, 1.0)))

    candidates.sort(key=lambda c: c.rrf_score, reverse=True)
    return candidates[:pool_limit]


def rrf_fuse(
    rankings: Iterable[list[tuple[int, float]]],
    k: int = 60,
) -> dict[int, float]:
    """Fuse multiple ranked lists into a single score per id.

    score(id) = sum over signals of 1 / (k + rank); no score normalization needed.
    """
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, (pid, _) in enumerate(ranking):
            scores[pid] += 1.0 / (k + rank + 1)
    return dict(scores)


def _vector_signal(
    qvec: list[float],
    places: PostgresPlaceRepository,
    constraints: ResolvedConstraints,
    limit: int,
) -> list[tuple[int, float]]:
    region_bbox: list[float] | None = None
    if constraints.bbox:
        w, s, e, n = constraints.bbox
        region_bbox = [s, w, n, e]
    found = places.by_embedding(qvec, limit=limit, region_bbox=region_bbox)
    return [(p.id, dist) for p, dist in found if p.id is not None]


def _keyword_signal(
    places: PostgresPlaceRepository,
    query: str,
    limit: int,
) -> list[tuple[int, float]]:
    if not query.strip():
        return []
    return [(p.id, 0.0) for p in places.keyword_search(query, limit=limit) if p.id is not None]


def _category_signal(
    places: PostgresPlaceRepository,
    constraints: ResolvedConstraints,
    categories: list[str],
    limit: int,
) -> list[tuple[int, float]]:
    """Return places matching the given categories, ordered by category priority.

    Explicit query categories come first so they outrank secondary ones in RRF.
    """
    db_cats = db_values(categories)
    if not db_cats:
        return []
    cat_order = {c: i for i, c in enumerate(categories)}
    rows = places.by_category(db_cats, limit)
    rows.sort(key=lambda p: cat_order.get(p.category or "", 99))
    return [(p.id, 0.0) for p in rows if p.id is not None]


def _hydrate(
    places: PostgresPlaceRepository,
    ids: list[int],
    scores: dict[int, float],
) -> list[Candidate]:
    return [
        candidate_of(place, scores.get(place.id or 0, 0.0))
        for place in places.get_by_ids(ids)
    ]


def _photo_model(place: Place) -> Photo | None:
    """The point's picture as the response model, or nothing at all.

    ``photo_of`` answers a plain dict (or None); the API model is ``Photo``.
    """
    raw = photo_of(place)
    return Photo(**raw) if raw else None


def candidate_of(place: Place, rrf_score: float) -> Candidate:
    """A retrieved place as the planner's working currency."""
    # A fetched row always has one; the model allows None only so a row can be
    # built before it is inserted.
    return Candidate(
        id=place.id or 0,
        name=place.name,
        category=place.category,
        lat=place.lat,
        lon=place.lon,
        blurb=place.blurb,
        fun_fact=place.fun_fact,
        fun_facts=parse_fun_facts(place.fun_facts),
        links=parse_links(place.links),
        opening_hours=place.opening_hours,
        ticket_price=place.ticket_price,
        town=place.town,
        district=place.district,
        photo=_photo_model(place),
        visit_minutes_db=place.visit_minutes,
        relevance=rrf_score,
        rrf_score=rrf_score,
    )


def apply_negative_filter(
    candidates: list[Candidate],
    constraints: ResolvedConstraints,
) -> list[Candidate]:
    """Drop places matching forbidden categories or forbidden keywords.

    Must-visit places bypass this filter (the user explicitly asked for them).
    """
    if not constraints.forbidden_categories and not constraints.forbidden_keywords:
        return candidates

    must_visit = set(constraints.must_visit_ids)
    out: list[Candidate] = []
    for c in candidates:
        if c.id in must_visit:
            out.append(c)
            continue
        if c.category and c.category in constraints.forbidden_categories:
            continue
        name_lower = c.name.lower()
        if any(kw.lower() in name_lower for kw in constraints.forbidden_keywords):
            continue
        out.append(c)
    return out

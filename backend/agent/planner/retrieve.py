"""Step 3 — Multi-signal candidate retrieval + RRF fusion.

Fuses vector, keyword, must and category signals; output has `rrf_score`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

import psycopg

from contracts.planner import Candidate, Photo, ResolvedConstraints
from domain import constants
from store.place_fields import parse_fun_facts, parse_links, parse_photo
from store.search import (
    _keyword_search,
    candidates_by_embedding,
    db_categories,
    fetch_points_by_ids,
    nearby_places,
)

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
    db: psycopg.Connection,
    lat: float,
    lon: float,
    limit: int,
) -> list[tuple[int, float]]:
    """Places around a known point (tourist GPS or a named town)."""
    rows = nearby_places(db, lat, lon, radius_km=constants.GEO_FOCUS_KM, limit=limit)
    return [(r["id"], 0.0) for r in rows]


def retrieve(
    constraints: ResolvedConstraints,
    query_embedding: list[float],
    db: psycopg.Connection,
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
        vector_signal = _vector_signal(query_embedding, db, constraints, limit=pool_limit)

    kw_query = " ".join(constraints.must_visit_keywords + [query_text]).strip()
    keyword_signal = _keyword_signal(db, kw_query, limit=pool_limit // 2) if kw_query else []

    must_signal = [(pid, 0.0) for pid in constraints.must_visit_ids]

    cat_signal: list[tuple[int, float]] = []
    if all_cats:
        cat_signal = _category_signal(db, constraints, all_cats, limit=pool_limit // 2)

    cat_first_signal: list[tuple[int, float]] = []
    if explicit_cat_kw:
        primary_cat = [explicit_cat_kw[0]]
        cat_first_signal = _category_signal(
            db, constraints, primary_cat, limit=pool_limit
        )

    near_signal: list[tuple[int, float]] = []
    if near is not None:
        near_signal = _nearby_signal(db, near[0], near[1], limit=pool_limit)

    fused = rrf_fuse(
        [vector_signal, keyword_signal, must_signal, cat_signal, cat_first_signal,
         near_signal],
        k=constants.RRF_K,
    )

    top_ids = sorted(fused, key=lambda pid: -fused[pid])[:pool_limit]

    candidates = _hydrate(db, top_ids, fused)

    if constants.NEGATIVE_FILTER_ENABLED:
        candidates = apply_negative_filter(candidates, constraints)

    if constraints.must_visit_ids:
        present_ids = {c.id for c in candidates}
        missing = [mid for mid in constraints.must_visit_ids if mid not in present_ids]
        if missing:
            extra_rows = fetch_points_by_ids(db, missing)
            for r in extra_rows:
                candidates.append(_row_to_candidate(r, fused.get(r["id"], 1.0)))

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
    db: psycopg.Connection,
    constraints: ResolvedConstraints,
    limit: int,
) -> list[tuple[int, float]]:
    region_bbox: list[float] | None = None
    if constraints.bbox:
        w, s, e, n = constraints.bbox
        region_bbox = [s, w, n, e]
    rows = candidates_by_embedding(db, qvec, limit=limit, region_bbox=region_bbox)
    return [(r["id"], float(r.get("cosine_dist", 0.0))) for r in rows]


def _keyword_signal(
    db: psycopg.Connection,
    query: str,
    limit: int,
) -> list[tuple[int, float]]:
    if not query.strip():
        return []
    rows = _keyword_search(db, query, limit=limit)
    return [(r["id"], 0.0) for r in rows]


def _category_signal(
    db: psycopg.Connection,
    constraints: ResolvedConstraints,
    categories: list[str],
    limit: int,
) -> list[tuple[int, float]]:
    """Return places matching the given categories, ordered by category priority.

    Explicit query categories come first so they outrank secondary ones in RRF.
    """
    db_cats = db_categories(categories)
    if not db_cats:
        return []
    cat_order = {c: i for i, c in enumerate(categories)}
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, category FROM places WHERE category = ANY(%s) LIMIT %s",
            (db_cats, limit),
        )
        rows = cur.fetchall()
    rows.sort(key=lambda r: cat_order.get(r[1], 99))
    return [(r[0], 0.0) for r in rows]


def _hydrate(
    db: psycopg.Connection,
    ids: list[int],
    scores: dict[int, float],
) -> list[Candidate]:
    rows = fetch_points_by_ids(db, ids)
    return [_row_to_candidate(r, scores.get(r["id"], 0.0)) for r in rows]


def _photo_model(row: dict) -> Photo | None:
    """The point's picture as the response model, or nothing at all.

    ``parse_photo`` answers a plain dict (or None); the API model is ``Photo``.
    """
    raw = parse_photo(row)
    return Photo(**raw) if raw else None


def _row_to_candidate(row: dict, rrf_score: float) -> Candidate:
    return Candidate(
        id=row["id"],
        name=row["name"],
        category=row.get("category"),
        lat=row["lat"],
        lon=row["lon"],
        blurb=row.get("blurb"),
        fun_fact=row.get("fun_fact"),
        fun_facts=parse_fun_facts(row.get("fun_facts")),
        links=parse_links(row.get("links")),
        opening_hours=row.get("opening_hours"),
        ticket_price=row.get("ticket_price"),
        town=row.get("town"),
        district=row.get("district"),
        photo=_photo_model(row),
        visit_minutes_db=row.get("visit_minutes"),
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

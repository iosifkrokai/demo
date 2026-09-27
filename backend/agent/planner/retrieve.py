"""Step 3 — Multi-signal candidate retrieval + RRF fusion.

Four parallel signals feed into a Reciprocal Rank Fusion:

  1. vector  — pgvector cosine distance (top-K by embedding)
  2. keyword — ILIKE name match (cheap, robust for specific queries)
  3. must    — explicit named_places from intent (force-include)
  4. category — direct category match (when intent supplies optional_categories)

Negative filter is applied AFTER fusion — places matching
forbidden_categories or forbidden_keywords are dropped before hydration.

Degraded mode: the vector signal is skipped whenever the caller passes an
empty `query_embedding` — no OPENROUTER_API_KEY, or an OpenRouter that
failed (see planner/pipeline.py `_openrouter_embed`).  The remaining five
signals are enough to build a route: explicit category words in the query
still drive category-first retrieval, and named places still resolve
through must_visit_ids / the geo anchor.

Output: list of Candidate with `rrf_score` populated. `relevance` is set to the
same RRF value: there is no re-scoring stage any more (the Jev reranker was
removed — it did not pay for itself on the golden set), so the fused retrieval
order *is* the relevance order.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

import psycopg

from .. import constants
from ..models import Candidate, ResolvedConstraints
from ..search import (
    _keyword_search,
    candidates_by_embedding,
    db_categories,
    fetch_points_by_ids,
    nearby_places,
)

# Russian query category keywords → LLM category taxonomy.
# When any of these words appear in the query text, the corresponding
# category gets a boosted priority in RRF fusion (category-first retrieval).
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
    "церковь": "церковь", "церкви": "церковь", "церкви": "церковь",
    "храм": "храм", "храмы": "храм",
    "усадьба": "усадьба", "усадьбы": "усадьба",
    "архитектура": "архитектура", "архитектурный": "архитектура",
    "инфраструктура": "инфраструктура",
    "крепость": "замок",
}


def _detect_category_keywords(query: str) -> list[str]:
    """Extract LLM-category labels from query text.

    This runs alongside the LLM intent extraction.  Even when the LLM
    classifies the query as "themed" or "vague", explicit category words
    like "замки" / "костёлы" / "дворцы" must still steer retrieval.
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
    # RRF consumes the rank order, not the distance value.
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

    # Detect explicit category keywords in the raw query (for Defect 2 fix).
    # These steer category-first retrieval even when the LLM intent is "themed".
    explicit_cat_kw = _detect_category_keywords(query_text)
    all_cats: list[str] = list({
        *(constraints.optional_categories or []),
        *explicit_cat_kw,
    })

    # ── Signal 1: vector (skipped when no embedding service is configured) ──
    vector_signal: list[tuple[int, float]] = []
    if query_embedding:
        vector_signal = _vector_signal(query_embedding, db, constraints, limit=pool_limit)

    # ── Signal 2: keyword (uses must_visit_keywords + raw query) ──
    kw_query = " ".join(constraints.must_visit_keywords + [query_text]).strip()
    keyword_signal = _keyword_signal(db, kw_query, limit=pool_limit // 2) if kw_query else []

    # ── Signal 3: must-visit (force-include at rank 0) ──
    must_signal = [(pid, 0.0) for pid in constraints.must_visit_ids]

    # ── Signal 4: category (boost by explicit category match) ──
    cat_signal: list[tuple[int, float]] = []
    if all_cats:
        cat_signal = _category_signal(db, constraints, all_cats, limit=pool_limit // 2)

    # ── Signal 4b: category-first retrieval (Defect 2 fix) ─────────────
    # When the query contains explicit category keywords (e.g. "замки"),
    # keyword search for those keywords returns places with the word in their
    # name — not places of that category.  E.g. "замки" matches "Замковая гора"
    # (not a castle) but misses "Лидский замок" (actual castle).
    # Category-first retrieval directly queries by category, ensuring the right
    # places surface even when keyword and vector signals are noisy.
    cat_first_signal: list[tuple[int, float]] = []
    if explicit_cat_kw:
        # Primary category only (first keyword in query order) to avoid diluting
        # with secondary categories.
        primary_cat = [explicit_cat_kw[0]]
        cat_first_signal = _category_signal(
            db, constraints, primary_cat, limit=pool_limit
        )

    # ── Signal 5: locality (only when we know the tourist's point / town) ──
    # A query naming a town often surfaces one place from that town and a
    # dozen from elsewhere; without this the walkable candidate set can
    # collapse to a single stop.
    near_signal: list[tuple[int, float]] = []
    if near is not None:
        near_signal = _nearby_signal(db, near[0], near[1], limit=pool_limit)

    # ── RRF fusion ──
    fused = rrf_fuse(
        [vector_signal, keyword_signal, must_signal, cat_signal, cat_first_signal,
         near_signal],
        k=constants.RRF_K,
    )

    # ── Top-N by fused score ──
    top_ids = sorted(fused, key=lambda pid: -fused[pid])[:pool_limit]

    # ── Hydrate ──
    candidates = _hydrate(db, top_ids, fused)

    # ── Negative filter ──
    if constants.NEGATIVE_FILTER_ENABLED:
        candidates = apply_negative_filter(candidates, constraints)

    # ── Must-visit guarantee: ensure they survived negative filter / pool truncation ──
    if constraints.must_visit_ids:
        present_ids = {c.id for c in candidates}
        missing = [mid for mid in constraints.must_visit_ids if mid not in present_ids]
        if missing:
            extra_rows = fetch_points_by_ids(db, missing)
            for r in extra_rows:
                candidates.append(_row_to_candidate(r, fused.get(r["id"], 1.0)))

    # Final ordering by rrf_score
    candidates.sort(key=lambda c: c.rrf_score, reverse=True)
    return candidates[:pool_limit]


# ─────────────────────────────────────────────────────────────────────────────
# RRF — Reciprocal Rank Fusion
# ─────────────────────────────────────────────────────────────────────────────

def rrf_fuse(
    rankings: Iterable[list[tuple[int, float]]],
    k: int = 60,
) -> dict[int, float]:
    """Fuse multiple ranked lists into a single score per id.

    score(id) = sum over signals of 1 / (k + rank)
    RRF (Cormack et al., 2009) is the standard for hybrid search — it does
    NOT require score normalization across signals.
    """
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, (pid, _) in enumerate(ranking):
            scores[pid] += 1.0 / (k + rank + 1)
    return dict(scores)


# ─────────────────────────────────────────────────────────────────────────────
# Individual signals
# ─────────────────────────────────────────────────────────────────────────────

def _vector_signal(
    qvec: list[float],
    db: psycopg.Connection,
    constraints: ResolvedConstraints,
    limit: int,
) -> list[tuple[int, float]]:
    # candidates_by_embedding returns cosine_dist; smaller is better.
    # We pass it as (id, dist) — the rank order is what RRF consumes,
    # the score itself is irrelevant.
    region_bbox: list[float] | None = None
    if constraints.bbox:
        w, s, e, n = constraints.bbox  # stored as (W, S, E, N)
        region_bbox = [s, w, n, e]     # search.py expects [S, W, N, E]
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

    When the query contains explicit category keywords (e.g. "замки"), those
    categories come FIRST so they outrank secondary categories in RRF fusion.
    """
    db_cats = db_categories(categories)
    if not db_cats:
        return []
    # Order by the category's position in the input list (primary first).
    cat_order = {c: i for i, c in enumerate(categories)}
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, category FROM places WHERE category = ANY(%s) LIMIT %s",
            (db_cats, limit),
        )
        rows = cur.fetchall()
    # Sort: primary categories first (lower rank → higher RRF score).
    rows.sort(key=lambda r: cat_order.get(r[1], 99))
    return [(r[0], 0.0) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Hydration + negative filter
# ─────────────────────────────────────────────────────────────────────────────

def _hydrate(
    db: psycopg.Connection,
    ids: list[int],
    scores: dict[int, float],
) -> list[Candidate]:
    rows = fetch_points_by_ids(db, ids)
    return [_row_to_candidate(r, scores.get(r["id"], 0.0)) for r in rows]


def parse_fun_facts(raw: str | None) -> list[str]:
    """Accept both '["a","b"]' JSON (region dataset) and 'a|b' pipe format (curated)."""
    raw = (raw or "").strip()
    if not raw:
        return []
    import json as _json
    if raw.startswith("["):
        try:
            return [str(x).strip() for x in _json.loads(raw) if str(x).strip()][:3]
        except _json.JSONDecodeError:
            pass
    return [f.strip() for f in raw.split("|") if f.strip()][:3]


def parse_links(raw: str | None) -> list[dict]:
    """Accept both '[{"title":...}]' JSON and 'title | url' pipe items."""
    raw = (raw or "").strip()
    if not raw:
        return []
    import json as _json
    if raw.startswith("["):
        try:
            return [x for x in _json.loads(raw) if isinstance(x, dict)][:4]
        except _json.JSONDecodeError:
            pass
    links: list[dict] = []
    for item in raw.split("|"):
        item = item.strip()
        if not item:
            continue
        if " | " in item:
            title, url = (s.strip() for s in item.split(" | ", 1))
            links.append({"title": title, "url": url})
        else:
            try:
                links.append(_json.loads(item))
            except Exception:
                pass
    return links[:4]


def parse_photo(row: dict) -> dict | None:
    """The point's picture with its credit — or nothing at all.

    All four fields are written by one resolution pass (`scripts/seed_photos.py`).
    A URL without its author and licence is deliberately not shown: Wikimedia
    files are licensed, and a credit-less image is a licence violation rather
    than a nice-to-have.
    """
    url = (row.get("photo_url") or "").strip()
    if not url:
        return None
    author = (row.get("photo_author") or "").strip()
    license_name = (row.get("photo_license") or "").strip()
    if not author or not license_name:
        return None
    return {
        "url": url,
        "author": author,
        "license": license_name,
        "source": (row.get("photo_source") or "").strip() or None,
    }


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
        photo=parse_photo(row),
        visit_minutes_db=row.get("visit_minutes"),
        relevance=rrf_score,  # no re-scoring stage: relevance = RRF fusion
        rrf_score=rrf_score,
    )


def apply_negative_filter(
    candidates: list[Candidate],
    constraints: ResolvedConstraints,
) -> list[Candidate]:
    """Drop places matching forbidden categories or forbidden keywords.

    Must-visit places bypass this filter (user explicitly asked for them).
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

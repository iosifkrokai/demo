"""Step 3 — Multi-signal candidate retrieval + RRF fusion.

Four parallel signals feed into a Reciprocal Rank Fusion:

  1. vector  — pgvector cosine distance (top-K by embedding)
  2. keyword — ILIKE name match (cheap, robust for specific queries)
  3. must    — explicit named_places from intent (force-include)
  4. category — direct category match (when intent supplies optional_categories)

Negative filter is applied AFTER fusion — places matching
forbidden_categories or forbidden_keywords are dropped before hydration.

Output: list of Candidate with `rrf_score` populated. The relevance field
is left as the rrf_score (downstream rerank overwrites it).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

import psycopg

from ..config import settings
from ..models import Candidate, ResolvedConstraints
from ..search import (
    _keyword_search,
    candidates_by_embedding,
    db_categories,
    fetch_points_by_ids,
)


def retrieve(
    constraints: ResolvedConstraints,
    query_embedding: list[float],
    db: psycopg.Connection,
    query_text: str = "",
) -> list[Candidate]:
    """Multi-signal → RRF → negative-filter → hydrate Candidate rows.

    Returns up to RETRIEVAL_POOL_SIZE candidates, ordered by rrf_score DESC.
    """
    pool_limit = settings.RETRIEVAL_POOL_SIZE

    # ── Signal 1: vector (skipped when no embedding service is configured) ──
    # Without it, the keyword signal below carries the retrieval alone.
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
    if constraints.optional_categories:
        cat_signal = _category_signal(db, constraints, limit=pool_limit // 2)

    # ── RRF fusion ──
    fused = rrf_fuse(
        [vector_signal, keyword_signal, must_signal, cat_signal],
        k=settings.RRF_K,
    )

    # ── Top-N by fused score ──
    top_ids = sorted(fused, key=lambda pid: -fused[pid])[:pool_limit]

    # ── Hydrate ──
    candidates = _hydrate(db, top_ids, fused)

    # ── Negative filter ──
    if settings.NEGATIVE_FILTER_ENABLED:
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
    limit: int,
) -> list[tuple[int, float]]:
    db_cats = db_categories(constraints.optional_categories)
    if not db_cats:
        return []
    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM places WHERE category = ANY(%s) LIMIT %s",
            (db_cats, limit),
        )
        return [(r[0], 0.0) for r in cur.fetchall()]


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


def _parse_fun_facts(raw: str | None) -> list[str]:
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


def _parse_links(raw: str | None) -> list[dict]:
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


def _row_to_candidate(row: dict, rrf_score: float) -> Candidate:
    return Candidate(
        id=row["id"],
        name=row["name"],
        category=row.get("category"),
        lat=row["lat"],
        lon=row["lon"],
        blurb=row.get("blurb"),
        fun_fact=row.get("fun_fact"),
        fun_facts=_parse_fun_facts(row.get("fun_facts")),
        links=_parse_links(row.get("links")),
        opening_hours=row.get("opening_hours"),
        ticket_price=row.get("ticket_price"),
        town=row.get("town"),
        district=row.get("district"),
        visit_minutes_db=row.get("visit_minutes"),
        relevance=rrf_score,  # before rerank, relevance = rrf score
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

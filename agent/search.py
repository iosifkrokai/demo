"""DB query helpers for the agent."""

from __future__ import annotations

from typing import Any

import psycopg

# LLM-extracted categories (agent/llm.py taxonomy) -> DB category values
# (the curated taxonomy in data/places_curated.csv). A query category maps
# to several DB values because the DB taxonomy is finer-grained for cult
# places.
CATEGORY_TO_DB: dict[str, list[str]] = {
    # the original 10 from llm.py
    "замок": ["замок"],
    "костёл": ["костёл"],
    "церковь": ["церковь"],
    "монастырь": ["монастырь"],
    "дворец": ["дворец", "усадьба"],
    "усадьба": ["усадьба", "дворец"],
    "парк": ["парк"],
    "музей": ["музей"],
    "памятник": ["памятник"],
    "городище": [],  # no gorodishche rows in curated set; placeholder
    # extended mapping from curated categories the user might ask about
    "храм": ["храм"],
    "архитектура": ["архитектура", "инфраструктура"],
    "инфраструктура": ["инфраструктура"],
    "кладбище": ["кладбище"],
}


def db_categories(categories: list[str]) -> list[str]:
    """Flatten LLM categories into distinct DB category values."""
    out: list[str] = []
    for c in categories or []:
        for db_cat in CATEGORY_TO_DB.get(c, []):
            if db_cat not in out:
                out.append(db_cat)
    return out


def _keyword_search(
    db: psycopg.Connection,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Exact + prefix keyword search on name column.

    Used as a hybrid fallback when the vector query is short (< 3 significant
    words) — embedding models trained on sentences give poor results for 1-2 words
    ("горисполком" vs "Здание Горисполкома"). ILIKE is fast enough on 76 rows.
    """
    # Extract significant words (≥ 3 chars, Russian/Latin letters)
    import re
    words = re.findall(r"[а-яёa-z]{3,}", query.lower())
    if not words:
        return []

    # Build ILIKE conditions: each word matches anywhere in name
    conditions = " OR ".join(["name ILIKE %s" for _ in words])
    params = [f"%{w}%" for w in words]
    # Also try normalized form: replace common prefixes/variants
    sql = f"""
        SELECT id, name, category, lat, lon, blurb, fun_fact, fun_facts, links, 0.0 AS cosine_dist
          FROM places
         WHERE {conditions}
         LIMIT %s
    """
    params.append(limit)
    with db.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def candidates_by_embedding(
    db: psycopg.Connection,
    qvec: list[float],
    limit: int = 50,
    region_bbox: list[float] | None = None,
    categories: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Top-K by vector cosine distance. Returns rows with cosine_dist column.

    Category filtering is intentionally skipped as a WHERE clause — the caller
    applies it as a soft score boost in agent.py instead. This prevents the
    bug where Qwen 1.5B mis-classifies short queries (e.g. "горисполком" →
    "инфраструктура") and the WHERE filter discards the only relevant result.

    Returns list of dicts with keys: id, name, category, lat, lon, blurb,
    fun_fact, cosine_dist.
    """
    bbox_clause = ""
    bbox_params: list[Any] = []
    if region_bbox and len(region_bbox) == 4:
        w, s, e, n = region_bbox
        bbox_clause = "   AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)"
        bbox_params = [w, s, e, n]

    # qvec is used twice: in the cosine_dist expression and in ORDER BY
    sql = f"""
        SELECT id, name, category, lat, lon, blurb, fun_fact, fun_facts, links,
               embedding <=> %s::vector AS cosine_dist
          FROM places
         WHERE embedding IS NOT NULL
        {bbox_clause}
         ORDER BY embedding <=> %s::vector
         LIMIT %s
    """
    params: list[Any] = [qvec] + bbox_params + [qvec, limit]

    with db.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def fetch_points_by_ids(db: psycopg.Connection, ids: list[int]) -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, name, category, lat, lon, blurb, fun_fact, fun_facts, links "
            "FROM places WHERE id = ANY(%s)",
            (ids,),
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    by_id = {r["id"]: r for r in rows}
    return [by_id[i] for i in ids if i in by_id]

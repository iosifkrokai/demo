"""DB query helpers for the agent.

Public surface:
  - candidates_by_embedding   — vector search (top-K by cosine)
  - _keyword_search           — ILIKE-based name match (short-query fallback)
  - fetch_points_by_ids       — hydrate Candidate objects from IDs
  - fetch_embeddings          — batch load embeddings by ID (for MMR)
  - db_categories             — map LLM categories to DB category values
"""

from __future__ import annotations

from typing import Any

import psycopg

# LLM-extracted categories (agent/llm.py taxonomy) -> DB category values
# (the curated taxonomy in data/places_curated.csv). A query category maps
# to several DB values because the DB taxonomy is finer-grained for cult
# places.
CATEGORY_TO_DB: dict[str, list[str]] = {
    "замок": ["замок"],
    "костёл": ["костёл"],
    "церковь": ["церковь"],
    "монастырь": ["монастырь"],
    "дворец": ["дворец", "усадьба"],
    "усадьба": ["усадьба", "дворец"],
    "парк": ["парк"],
    "музей": ["музей"],
    "памятник": ["памятник"],
    "городище": [],
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
    words). ILIKE is fast enough on 76 rows.
    """
    import re
    words = re.findall(r"[а-яёa-z]{3,}", query.lower())
    if not words:
        return []

    conditions = " OR ".join(["name ILIKE %s" for _ in words])
    params = [f"%{w}%" for w in words]
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
) -> list[dict[str, Any]]:
    """Top-K by vector cosine distance. Returns rows with cosine_dist column.

    Note: category filtering is intentionally omitted — the caller applies
    it as a soft score boost, not a WHERE clause. This prevents the bug
    where a misclassified query drops the only relevant result.
    """
    bbox_clause = ""
    bbox_params: list[Any] = []
    if region_bbox and len(region_bbox) == 4:
        # NOTE: caller passes [south, west, north, east]; ST_MakeEnvelope takes (W,S,E,N)
        s, w, n, e = region_bbox
        bbox_clause = "   AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)"
        bbox_params = [w, s, e, n]

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


def fetch_embeddings(db: psycopg.Connection, ids: list[int]) -> dict[int, list[float]]:
    """Batch-load embeddings for MMR diversity step.

    Returns: {id: vector_as_list}. Missing IDs are silently skipped.
    Caller should pass only IDs that have non-null embeddings (we filter
    in the SQL WHERE clause just in case).
    """
    if not ids:
        return {}
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, embedding::text FROM places "
            "WHERE id = ANY(%s) AND embedding IS NOT NULL",
            (ids,),
        )
        out: dict[int, list[float]] = {}
        for pid, emb_text in cur.fetchall():
            # pgvector returns '[0.1,0.2,...]' as text in some psycopg versions
            if isinstance(emb_text, str):
                emb_text = emb_text.strip("[]")
                try:
                    out[pid] = [float(x) for x in emb_text.split(",") if x.strip()]
                except ValueError:
                    continue
            else:
                # numpy array or list — convert via tolist if available
                arr = emb_text
                if hasattr(arr, "tolist"):
                    arr = arr.tolist()
                out[pid] = list(arr)
        return out


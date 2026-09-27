"""DB query helpers for the agent.

Public surface:
  - candidates_by_embedding   — vector search (top-K by cosine)
  - _keyword_search           — ILIKE-based name match (short-query fallback)
  - fetch_points_by_ids       — hydrate Candidate objects from IDs
  - fetch_embeddings          — batch load embeddings by ID (for MMR)
  - db_categories             — map query categories to DB category values
"""

from __future__ import annotations

import math
import re
from typing import Any

import psycopg

from .taxonomy import db_values


def db_categories(categories: list[str]) -> list[str]:
    """Map query categories (codes, aliases, any inflection) to DB values.

    Thin wrapper over ``taxonomy.db_values`` — the canonical taxonomy is the
    only place a category is defined, so this module keeps no second map.
    Unknown categories resolve to nothing and are dropped (an unknown model
    category must not widen the SQL filter).
    """
    return db_values(categories)


CATEGORY_COLS = "id, name, category, lat, lon, blurb, fun_fact, fun_facts, links, " \
               "opening_hours, ticket_price, town, district, visit_minutes, " \
               "photo_url, photo_author, photo_license, photo_source"


def _keyword_search(
    db: psycopg.Connection,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Exact + prefix keyword search on name and town columns.

    Used as a hybrid signal when the vector query is short (< 3 significant
    words) and as the ONLY signal when OpenRouter embeddings are unavailable
    (no OPENROUTER_API_KEY) — the pipeline's graceful-degradation path.

    Matching is ILIKE + pg_trgm word-similarity, because Russian inflection
    breaks plain ILIKE ("новогрудка" vs "Новогрудок", "костёлы" vs "костёл").
    Requires the pg_trgm extension (db/migrations/0003_trgm_search.sql).
    """

    words = re.findall(r"[а-яёa-z]{3,}\d*", query.lower())
    words = [w for w in words if len(w) >= 3]
    if not words:
        return []

    per_word = " OR ".join(
        [
            "name ILIKE %s OR town ILIKE %s OR district ILIKE %s",
            # NB: '%%>' — psycopg escapes literal % as %%; the operator is %>
            "name %%> %s OR town %%> %s OR district %%> %s",
        ]
    )
    conditions = " OR ".join([per_word] * len(words))
    # Rank by the best trigram similarity across the matched columns — the
    # LIMIT must cut AFTER ranking, otherwise top-1 is a random row.
    word_sim = [
        "GREATEST(similarity(name, %s), word_similarity(%s, name), "
        "similarity(town, %s), word_similarity(%s, town))"
    ] * len(words)
    # SUM across words: a row matching ALL query words outranks a row that
    # matches only one word perfectly ("Мирский замок" > "Старый замок").
    order_expr = "(" + ") + (".join(word_sim) + ")"
    params: list[Any] = []
    for w in words:
        params.extend([f"%{w}%"] * 3)
        params.extend([w] * 3)
    sql = f"""
        SELECT {CATEGORY_COLS}, 0.0 AS cosine_dist
          FROM places
         WHERE {conditions}
         ORDER BY {order_expr} DESC
         LIMIT %s
    """
    # ORDER BY consumes 4 params per word (similarity/word_similarity ×
    # name/town) in word-major order: [w1,w1,w1,w1, w2,w2,w2,w2].
    params.extend(w for w in words for _ in range(4))
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
        SELECT {CATEGORY_COLS},
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
            f"SELECT {CATEGORY_COLS} "
            "FROM places WHERE id = ANY(%s)",
            (ids,),
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    by_id = {r["id"]: r for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def nearby_places(
    db: psycopg.Connection,
    lat: float,
    lon: float,
    radius_km: float = 12.0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Places within radius_km of (lat, lon), nearest first.

    Retrieval is text-driven, so a query that names a town ("пешеходный
    маршрут Новогрудок, замковая гора, ...") can surface one place from that
    town and a dozen from elsewhere in the voblast.  When we know where the
    tourist is (GPS) or which town they named, this signal guarantees the pool
    actually contains places around that point.
    """
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    with db.cursor() as cur:
        cur.execute(
            f"SELECT {CATEGORY_COLS} FROM places "
            "WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
            "ORDER BY (lat - %s) * (lat - %s) + (lon - %s) * (lon - %s) "
            "LIMIT %s",
            (
                lat - dlat, lat + dlat, lon - dlon, lon + dlon,
                lat, lat, lon, lon,
                limit,
            ),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


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



def _name_match_search(
    db: psycopg.Connection,
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Search POIs by NAME only (no town/district), with trigram similarity.

    Used by resolve._resolve_named_places to determine whether a named-place
    token corresponds to a real tourist POI (must_visit) or just an area name.

    Returns rows with an extra '_name_sim' float column (similarity score).
    """

    words = re.findall(r"[а-яёa-z]{2,}", query.lower())
    words = [w for w in words if len(w) >= 2]
    if not words:
        return []

    # Search on name only (ILIKE + trigram), not town/district.
    per_word = "name ILIKE %s OR name %%> %s"
    conditions = " OR ".join([per_word] * len(words))

    word_sims = [
        "GREATEST(similarity(name, %s), word_similarity(%s, name))"
    ] * len(words)
    order_expr = "(" + ") + (".join(word_sims) + ")"

    params: list[Any] = []
    for w in words:
        params.extend([f"%{w}%", w])
    for w in words:
        params.extend([w, w])

    sql = f"""
        SELECT {CATEGORY_COLS},
               ({order_expr}) AS _name_sim
          FROM places
         WHERE {conditions}
         ORDER BY _name_sim DESC
         LIMIT %s
    """
    params.append(limit)

    with db.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

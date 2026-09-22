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


def candidates_by_embedding(
    db: psycopg.Connection,
    qvec: list[float],
    limit: int = 50,
    region_bbox: list[float] | None = None,
    categories: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Top-K by vector cosine. Optional bbox filter via PostGIS and
    category filter (DB values from db_categories()); pass None to disable."""
    sql = [
        "SELECT id, name, category, lat, lon, blurb",
        "  FROM places",
        " WHERE embedding IS NOT NULL",
    ]
    params: list[Any] = []
    if region_bbox and len(region_bbox) == 4:
        s, w, n, e = region_bbox
        sql.append("   AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)")
        params.extend([w, s, e, n])
    if categories:
        sql.append("   AND category = ANY(%s)")
        params.append(categories)
    sql.append(" ORDER BY embedding <=> %s::vector LIMIT %s")
    params.extend([qvec, limit])
    with db.cursor() as cur:
        cur.execute("\n".join(sql), params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def fetch_points_by_ids(db: psycopg.Connection, ids: list[int]) -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, name, category, lat, lon, blurb FROM places WHERE id = ANY(%s)",
            (ids,),
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    by_id = {r["id"]: r for r in rows}
    return [by_id[i] for i in ids if i in by_id]

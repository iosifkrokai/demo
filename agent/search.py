"""DB query helpers for the agent."""

from __future__ import annotations

from typing import Any

import psycopg


def candidates_by_embedding(
    db: psycopg.Connection,
    qvec: list[float],
    limit: int = 50,
    region_bbox: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Top-K by vector cosine. Optional bbox filter via PostGIS."""
    sql = [
        "SELECT id, name, category, lat, lon",
        "  FROM places",
        " WHERE embedding IS NOT NULL",
    ]
    params: list[Any] = []
    if region_bbox and len(region_bbox) == 4:
        s, w, n, e = region_bbox
        sql.append("   AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)")
        params.extend([w, s, e, n])
    sql.append(" ORDER BY embedding <=> %s::vector LIMIT %s")
    params.extend([qvec, limit])
    with db.cursor() as cur:
        cur.execute("\n".join(sql), params)
        return [dict(r) for r in cur.fetchall()]


def fetch_points_by_ids(db: psycopg.Connection, ids: list[int]) -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, name, category, lat, lon FROM places WHERE id = ANY(%s)",
            (ids,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    by_id = {r["id"]: r for r in rows}
    return [by_id[i] for i in ids if i in by_id]

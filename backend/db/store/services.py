"""Secondary points: the services you pass on the way, never the reason to go.

A service is never a stop, and geometry is measured, never guessed as a detour.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any

log = logging.getLogger(__name__)

MAX_OFF_LINE_M: dict[str, float] = {
    "pedestrian": 150.0,
    "bicycle": 250.0,
    "car": 400.0,
    "any": 250.0,
}
DEFAULT_PROFILE = "pedestrian"

_PROFILE_ALIASES: dict[str, str] = {
    "pedestrian": "pedestrian",
    "bicycle": "bicycle",
    "auto": "car",
    "car": "car",
    "truck": "car",
    "bus": "car",
    "motorcycle": "car",
    "motor_scooter": "car",
    "any": "any",
}


def threshold_for(profile: str, max_off_line_m: float | None = None) -> float:
    """How far off the line this profile is willing to go, in metres."""
    if max_off_line_m is not None:
        return float(max_off_line_m)
    key = _PROFILE_ALIASES.get(profile, DEFAULT_PROFILE)
    return MAX_OFF_LINE_M[key]

MAX_SERVICES = 12
MAX_LINE_POINTS = 2000


def service_codes(categories: Iterable[str] | None = None) -> list[str]:
    """The categories that count as services, from the taxonomy alone.

    Unknown or non-service codes are dropped rather than trusted.
    """
    from reference.taxonomy import all_categories

    known = [
        cat.code
        for cat in all_categories()
        if getattr(cat, "role", None) == "service"
    ]
    if categories is None:
        return known
    return [code for code in categories if code in known]


def route_line(shape: Any) -> dict:
    """Validate a GeoJSON LineString and return it unchanged.

    Raises `ValueError` with a machine-readable reason, never an empty answer.
    """
    if not isinstance(shape, dict) or shape.get("type") != "LineString":
        raise ValueError("shape_not_linestring")
    coords = shape.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        raise ValueError("shape_needs_two_points")
    if len(coords) > MAX_LINE_POINTS:
        raise ValueError("shape_too_long")
    for point in coords:
        if (
            not isinstance(point, (list, tuple))
            or len(point) < 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in point[:2])
        ):
            raise ValueError("shape_bad_point")
    return shape


def _item(row: dict[str, Any], line_m: float) -> dict[str, Any]:
    """One service, with its measurement named for what it actually is."""
    hours = (row.get("opening_hours") or "").strip()
    fraction = float(row.get("along_fraction") or 0.0)
    return {
        "id": row["id"],
        "source_url": row["source_url"],
        "name": row["name"],
        "category": row["category"],
        "town": row.get("town"),
        "lat": row["lat"],
        "lon": row["lon"],
        "opening_hours": hours or None,
        "hours_known": bool(hours),
        "off_line_m": round(float(row.get("off_line_m") or 0.0)),
        "along_m": round(max(0.0, min(1.0, fraction)) * line_m),
        "along_fraction": round(fraction, 4),
        "detour_confirmed": False,
    }


class _SQL:
    """The one query this module runs, kept in one place for reading."""

    BESIDE_LINE = """
        WITH line AS (
            SELECT ST_GeomFromGeoJSON(%s) AS geom
        )
        SELECT p.id, p.source_url, p.name, p.category, p.town, p.lat, p.lon,
               p.opening_hours,
               ST_Distance(p.geom::geography, l.geom::geography) AS off_line_m,
               ST_LineLocatePoint(l.geom, p.geom) AS along_fraction,
               ST_Length(l.geom::geography) AS line_m
        FROM places p
        CROSS JOIN line l
        WHERE p.category = ANY(%s)
          AND p.geom IS NOT NULL
          AND ST_DWithin(p.geom::geography, l.geom::geography, %s)
        ORDER BY along_fraction ASC, off_line_m ASC
        LIMIT %s
    """


def services_along(
    conn: Any,
    shape: dict,
    *,
    categories: Sequence[str] | None = None,
    profile: str = DEFAULT_PROFILE,
    max_off_line_m: float | None = None,
    limit: int = MAX_SERVICES,
) -> dict[str, Any]:
    """Services lying beside a route line, ordered along it.

    Never mixes a service into the route's stops; the caller shows it.
    """
    import json

    from psycopg.rows import dict_row

    line = route_line(shape)
    codes = service_codes(categories)
    if not codes:
        return _empty(codes, profile, max_off_line_m, reason="no_service_categories")

    threshold = threshold_for(profile, max_off_line_m)
    cap = max(1, int(limit))

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            _SQL.BESIDE_LINE,
            (json.dumps(line), codes, threshold, cap),
        )
        rows = [dict(row) for row in cur.fetchall()]

    line_m = float(rows[0]["line_m"]) if rows else 0.0
    items = [_item(row, line_m) for row in rows]
    log.info(
        "services_along: %d of at most %d beside a %.0f m line (%.0f m off-line gate)",
        len(items),
        cap,
        line_m,
        threshold,
    )
    return {
        "items": items,
        "measured": "distance_to_line",
        "not_measured": "detour_walking_time",
        "detour_confirmed": False,
        "profile": profile,
        "categories": codes,
        "max_off_line_m": threshold,
        "line_m": round(line_m),
        "result_cap": cap,
        "capped": len(items) >= cap,
    }


def _empty(
    codes: Sequence[str], profile: str, max_off_line_m: float | None, *, reason: str
) -> dict[str, Any]:
    """An empty answer that says why it is empty."""
    return {
        "items": [],
        "measured": "distance_to_line",
        "not_measured": "detour_walking_time",
        "detour_confirmed": False,
        "profile": profile,
        "categories": list(codes),
        "max_off_line_m": max_off_line_m,
        "line_m": 0,
        "result_cap": MAX_SERVICES,
        "capped": False,
        "reason": reason,
    }

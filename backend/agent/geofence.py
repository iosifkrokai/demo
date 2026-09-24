"""Country geofence for the places table.

The OSM ingest pulls by bounding box, and that box (52.75,23.35,54.80,27.00)
covers a slice of Lithuania and Poland — Vilnius landmarks ended up in the DB
labelled as "Островецкий район" and were returned for Grodno queries. Everything
that enters the DB now has to pass this check first.

The polygon lives in data/belarus_border.json: Natural Earth 10m admin-0
(public domain), RDP-simplified to ~1 km.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

BORDER_PATH = Path(__file__).resolve().parent.parent / "data" / "belarus_border.json"


@lru_cache(maxsize=1)
def _rings() -> tuple[tuple[tuple[float, float], ...], ...]:
    """Rings as (lon, lat) tuples, plus a bbox per ring for a cheap pre-check."""
    data = json.loads(BORDER_PATH.read_text())
    return tuple(tuple((float(lon), float(lat)) for lon, lat in ring) for ring in data["rings"])


@lru_cache(maxsize=8)
def _bboxes() -> tuple[tuple[float, float, float, float], ...]:
    return tuple(
        (
            min(lon for lon, _ in ring),
            min(lat for _, lat in ring),
            max(lon for lon, _ in ring),
            max(lat for _, lat in ring),
        )
        for ring in _rings()
    )


def _in_ring(lat: float, lon: float, ring: tuple[tuple[float, float], ...]) -> bool:
    """Ray casting: count crossings of the horizontal ray going east from the point."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_cross:
                inside = not inside
    return inside


def inside_belarus(lat: float, lon: float) -> bool:
    """True when the point falls inside the Belarus border polygon."""
    if lat is None or lon is None:
        return False
    for ring, (min_lon, min_lat, max_lon, max_lat) in zip(_rings(), _bboxes(), strict=True):
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        if _in_ring(lat, lon, ring):
            return True
    return False


KEEP_PATH = Path(__file__).resolve().parent.parent / "data" / "belarus_border_keep.json"


@lru_cache(maxsize=1)
def _keeps() -> tuple[tuple[float, float, float], ...]:
    """(lat, lon, radius_m) of POIs that are in Belarus but outside the polygon."""
    data = json.loads(KEEP_PATH.read_text())
    r = float(data.get("radius_m", 300))
    return tuple((float(p["lat"]), float(p["lon"]), r) for p in data["points"])


def inside_project_area(lat: float, lon: float) -> bool:
    """Belarus, plus the handful of border POIs the simplified polygon cuts off.

    This is the filter the ingest and the purge both use, so re-running either
    one keeps exactly the same set of places.
    """
    if inside_belarus(lat, lon):
        return True
    for k_lat, k_lon, radius_m in _keeps():
        if abs(lat - k_lat) * 111_320 > radius_m:
            continue
        if abs(lon - k_lon) * 111_320 * 0.6 > radius_m:  # ~cos(53.7°)
            continue
        d_lat = (lat - k_lat) * 111_320
        d_lon = (lon - k_lon) * 111_320 * 0.6
        if (d_lat * d_lat + d_lon * d_lon) ** 0.5 <= radius_m:
            return True
    return False

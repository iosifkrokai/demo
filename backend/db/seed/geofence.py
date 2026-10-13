"""The project-area predicate, as the seed needs it."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from core.paths import GEO_DIR

GRODNO_BORDER_PATH = GEO_DIR / "grodno_border.json"
KEEP_PATH = GEO_DIR / "belarus_border_keep.json"

_LAT_M_PER_DEG = 111_320
_LON_M_PER_DEG = 111_320 * 0.6
_DEFAULT_EXCEPTION_RADIUS_M = 300.0


@lru_cache(maxsize=1)
def _grodno_rings() -> tuple[tuple[tuple[float, float], ...], ...]:
    """Rings as (lon, lat) tuples, loaded from the geoBoundaries ADM1 file."""
    data = json.loads(GRODNO_BORDER_PATH.read_text(encoding="utf-8"))
    return tuple(tuple((float(lon), float(lat)) for lon, lat in ring) for ring in data["rings"])


@lru_cache(maxsize=1)
def _exceptions() -> tuple[dict[str, Any], ...]:
    """Documented boundary exceptions (belarus_border_keep.json) as data."""
    data = json.loads(KEEP_PATH.read_text(encoding="utf-8"))
    default_radius = float(data.get("radius_m", _DEFAULT_EXCEPTION_RADIUS_M))
    return tuple(
        {
            "name": point.get("name", ""),
            "lat": float(point["lat"]),
            "lon": float(point["lon"]),
            "radius_m": float(point.get("radius_m", default_radius)),
        }
        for point in data["points"]
    )


def point_in_ring(lat: float, lon: float, ring: tuple[tuple[float, float], ...]) -> bool:
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


def _within_radius(
    lat: float, lon: float, center_lat: float, center_lon: float, radius_m: float
) -> bool:
    if abs(lat - center_lat) * _LAT_M_PER_DEG > radius_m:
        return False
    if abs(lon - center_lon) * _LON_M_PER_DEG > radius_m:
        return False
    d_lat = (lat - center_lat) * _LAT_M_PER_DEG
    d_lon = (lon - center_lon) * _LON_M_PER_DEG
    return (d_lat * d_lat + d_lon * d_lon) ** 0.5 <= radius_m


def inside_project_area(lat: float | None, lon: float | None) -> bool:
    """Grodno Oblast plus the documented exceptions; False for unknown points."""
    if lat is None or lon is None:
        return False
    if any(point_in_ring(lat, lon, ring) for ring in _grodno_rings()):
        return True
    return any(
        _within_radius(lat, lon, exc["lat"], exc["lon"], exc["radius_m"]) for exc in _exceptions()
    )

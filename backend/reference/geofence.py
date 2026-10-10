"""Country and Grodno ADM1 geofence for the places table.

The country polygon is Natural Earth 10m, RDP-simplified to ~1 km.
"""

from __future__ import annotations

import json
from functools import lru_cache

from core.paths import GEO_DIR
from reference import areas
from reference.areas import point_in_ring

BORDER_PATH = GEO_DIR / "belarus_border.json"


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


def inside_belarus(lat: float, lon: float) -> bool:
    """True when the point falls inside the Belarus border polygon."""
    if lat is None or lon is None:
        return False
    for ring, (min_lon, min_lat, max_lon, max_lat) in zip(_rings(), _bboxes(), strict=True):
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        if point_in_ring(lat, lon, ring):
            return True
    return False


def inside_project_area(lat: float, lon: float) -> bool:
    """Grodno ADM1, with the previously verified border-POI exceptions.

    Delegates to reference.areas.in_project_area, the single shared predicate.
    """
    return areas.in_project_area(lat, lon)

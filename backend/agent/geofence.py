"""Country and Grodno ADM1 geofence for the places table.

The OSM ingest pulls by bounding box, and that box (52.75,23.35,54.80,27.00)
covers a slice of Lithuania and Poland — Vilnius landmarks ended up in the DB
labelled as "Островецкий район" and were returned for Grodno queries. Everything
that enters the DB now has to pass this check first.

The country polygon lives in data/belarus_border.json: Natural Earth 10m
admin-0 (public domain), RDP-simplified to ~1 km.

The Grodno (project-area) predicate moved to agent/areas.py so that import-time
validation and query-time filtering share ONE implementation (spec 002 §5:
area boundaries and documented exceptions are a single source of truth). This
module keeps its historical public names: inside_belarus stays here (it is the
country check, not a project area), and inside_project_area delegates to
agent.areas.in_project_area without changing behaviour.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import areas
from .areas import point_in_ring

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

    Delegates to agent.areas.in_project_area — the single predicate shared by
    every writer and the purge, and by query-time filtering. The country
    polygon alone includes Brest/Minsk POIs inside the ingest bbox.
    """
    return areas.in_project_area(lat, lon)

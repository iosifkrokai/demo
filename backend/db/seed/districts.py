"""Which district a coordinate falls in, from the committed boundaries.

The ``district`` column used to be "the nearest raion centre", and a border is not a
Voronoi of centres: measured against these boundaries, 1108 of 7466 committed rows sat in a
neighbouring district. The boundaries are now committed data
(``db/data/geo/districts_border.json``, OSM ``admin_level=6``, ODbL) and the answer is a
point-in-polygon: holes are respected, and a city wins over the district around it.

A point outside every polygon answers ``None`` — an honest unknown the caller keeps its own
fallback for, rather than a district invented from distance.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

BOUNDARY_PATH = Path(__file__).resolve().parent.parent / "data" / "geo" / "districts_border.json"
METRES_PER_DEGREE = 111_320.0
SNAP_M = 500.0
SNAP_DEG = 0.01

Ring = list[list[float]]
District = dict[str, Any]


@lru_cache(maxsize=1)
def districts() -> tuple[District, ...]:
    """The committed polygons, in the order the file lists them (sorted by slug)."""
    return tuple(json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))["districts"])


@lru_cache(maxsize=1)
def _boxes() -> tuple[tuple[District, tuple[float, float, float, float]], ...]:
    boxes = []
    for district in districts():
        xs = [point[0] for ring in _all_rings(district) for point in ring]
        ys = [point[1] for ring in _all_rings(district) for point in ring]
        boxes.append((district, (min(xs), min(ys), max(xs), max(ys))))
    return tuple(boxes)


def _all_rings(district: District) -> list[Ring]:
    return [*district["rings"], *district.get("holes", [])]


def _inside_ring(ring: Ring, lon: float, lat: float) -> bool:
    inside = False
    for index in range(len(ring) - 1):
        x1, y1 = ring[index]
        x2, y2 = ring[index + 1]
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _inside(district: District, lon: float, lat: float) -> bool:
    if not any(_inside_ring(ring, lon, lat) for ring in district["rings"]):
        return False
    return not any(_inside_ring(ring, lon, lat) for ring in district.get("holes", []))


def _ring_wkt(ring: Ring) -> str:
    points = [*ring, ring[0]] if ring[0] != ring[-1] else ring
    return "(" + ", ".join(f"{lon} {lat}" for lon, lat in points) + ")"


def wkt(slug: str) -> str | None:
    """The district's boundary as WKT, or ``None`` for a slug the file does not hold.

    A record is a shape, not a box: the city of Grodno is a hole in the district around
    it, and every hole is cut out of the shell that holds it.
    """
    district = next((d for d in districts() if d["slug"] == slug), None)
    if district is None:
        return None
    holes = district.get("holes") or []
    polygons = []
    for shell in district["rings"]:
        parts = [_ring_wkt(shell)]
        parts += [_ring_wkt(hole) for hole in holes if _inside_ring(shell, *hole[0])]
        polygons.append("(" + ", ".join(parts) + ")")
    return "MULTIPOLYGON(" + ", ".join(polygons) + ")"


def district_at(lat: float, lon: float) -> str | None:
    """The district name this point stands in, or ``None`` beyond :data:`SNAP_M` of them all."""
    hits = [
        district
        for district, (west, south, east, north) in _boxes()
        if west <= lon <= east and south <= lat <= north and _inside(district, lon, lat)
    ]
    for district in hits:
        if district.get("kind") == "city":
            return str(district["name_ru"])
    if hits:
        return str(hits[0]["name_ru"])
    return _nearest_within_snap(lat, lon)


def _nearest_within_snap(lat: float, lon: float) -> str | None:
    """The ring a point in the hairline between two of them stands closest to.

    OSM's district rings do not tile perfectly: 22 committed rows sit inside the oblast and
    inside no district at all, a hundred to seven hundred metres from the nearest one.
    Naming that neighbour beats falling back to the nearest raion centre ten kilometres away.
    """
    candidates = [
        district
        for district, (west, south, east, north) in _boxes()
        if west - SNAP_DEG <= lon <= east + SNAP_DEG and south - SNAP_DEG <= lat <= north + SNAP_DEG
    ]
    closest, name = SNAP_M, None
    for district in candidates:
        for ring in _all_rings(district):
            for index in range(len(ring) - 1):
                metres = _segment_distance_m(lat, lon, ring[index], ring[index + 1])
                if metres < closest:
                    closest, name = metres, str(district["name_ru"])
    return name


def _segment_distance_m(lat: float, lon: float, start: list[float], end: list[float]) -> float:
    scale = math.cos(math.radians(lat))
    ax, ay = (start[0] - lon) * scale * METRES_PER_DEGREE, (start[1] - lat) * METRES_PER_DEGREE
    bx, by = (end[0] - lon) * scale * METRES_PER_DEGREE, (end[1] - lat) * METRES_PER_DEGREE
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    along = 0.0 if length == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length))
    return math.hypot(ax + along * dx, ay + along * dy)

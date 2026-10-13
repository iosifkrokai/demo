"""Distance between two points: keep a walking route local around its anchor."""

from __future__ import annotations

import math

from routing.models import Candidate

TRACE_NAMES_MAX = 15


def distance_m(a: Candidate, b: Candidate) -> float:
    """Equirectangular distance in metres (fine at city scale)."""
    return distance_pt_m(a.lat, a.lon, b.lat, b.lon)


def distance_pt_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular distance in metres (fine at city scale)."""

    lat_mid = math.radians((lat1 + lat2) / 2)
    dx = math.radians(lon1 - lon2) * 6_371_000 * math.cos(lat_mid)
    dy = math.radians(lat1 - lat2) * 6_371_000
    return math.hypot(dx, dy)

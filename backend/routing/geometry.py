"""Distance between two points: keep a walking route local around its anchor."""

from __future__ import annotations

import math

from core import constants
from routing.models import Candidate


def distance_m(a: Candidate, b: Candidate) -> float:
    """Equirectangular distance in metres (fine at city scale)."""
    return distance_pt_m(a.lat, a.lon, b.lat, b.lon)


def distance_pt_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular distance in metres (fine at city scale)."""

    lat_mid = math.radians((lat1 + lat2) / 2)
    dx = math.radians(lon1 - lon2) * constants.EARTH_RADIUS_M * math.cos(lat_mid)
    dy = math.radians(lat1 - lat2) * constants.EARTH_RADIUS_M
    return math.hypot(dx, dy)

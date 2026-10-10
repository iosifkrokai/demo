"""Snap locations onto the routing network, memoised per rounded coordinate."""

from __future__ import annotations

import json
import logging

from core import constants
from core.config import settings
from core.errors import UpstreamUnavailable

from . import http
from .constants import LOCATE_RADIUS_M, LOCATE_SNAP_RADIUS_M

logger = logging.getLogger(__name__)

SNAP_CACHE: dict[tuple[int, int], tuple[float, float]] = {}


def _locate_snap(
    location: dict, costing: str, timeout: float, radius: int
) -> tuple[float, float] | None:
    payload = {
        "locations": [{**location, "radius": radius}],
        "costing": costing,
    }
    try:
        body = http.request_with_retry(
            "GET",
            f"{settings.VALHALLA_URL.rstrip('/')}/locate",
            params={"json": json.dumps(payload, separators=(",", ":"))},
            timeout=timeout,
        )
    except UpstreamUnavailable:
        return None
    entry = body[0] if isinstance(body, list) and body else None
    edges = (entry or {}).get("edges") or []
    if not edges:
        return None
    edge = edges[0]
    if "correlated_lat" not in edge or "correlated_lon" not in edge:
        return None
    return float(edge["correlated_lat"]), float(edge["correlated_lon"])


def snap_locations(
    locations: list[dict],
    costing: str = "pedestrian",
    timeout: float | None = None,
    radius: int = LOCATE_SNAP_RADIUS_M,
) -> list[dict]:
    """Snap every location onto the routing network; keeps lat/lon dict shape.

    Results are memoised per rounded coordinate.
    """
    out: list[dict] = []
    for loc in locations:
        lat, lon = float(loc["lat"]), float(loc["lon"])
        key = (round(lat * 100_000), round(lon * 100_000))
        if key not in SNAP_CACHE:
            snapped = _locate_snap(
                {"lat": lat, "lon": lon},
                costing,
                timeout or constants.VALHALLA_TIMEOUT_S,
                radius,
            )
            SNAP_CACHE[key] = snapped or (lat, lon)
        snap_lat, snap_lon = SNAP_CACHE[key]
        out.append({**loc, "lat": snap_lat, "lon": snap_lon})
    return out


def _snappable(location: dict, costing: str, timeout: float | None) -> bool:
    """Ask /locate whether this point can be snapped onto the network.

    Timeouts and 5xx count as snappable — a hiccup must not drop a stop.
    """
    payload = {
        "locations": [{**location, "radius": LOCATE_RADIUS_M}],
        "costing": costing,
    }
    try:
        body = http.request_with_retry(
            "GET",
            f"{settings.VALHALLA_URL.rstrip('/')}/locate",
            params={"json": json.dumps(payload, separators=(",", ":"))},
            timeout=timeout or constants.VALHALLA_TIMEOUT_S,
        )
    except UpstreamUnavailable:
        return True
    if not body:
        return False
    entry = body[0] if isinstance(body, list) else body
    return bool(entry.get("edges"))


def _drop_unsnappable(locations: list[dict], costing: str, timeout: float | None) -> list[dict]:
    """Keep only the locations /locate can place on the routing network.

    One off-graph POI makes Valhalla answer 500 for the whole request.
    """
    keep = [loc for loc in locations if _snappable(loc, costing, timeout)]
    if len(keep) != len(locations):
        logger.warning(
            "snap: dropped %d of %d location(s) with no edge on the network",
            len(locations) - len(keep),
            len(locations),
        )
    return keep

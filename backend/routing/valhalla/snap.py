"""Snap locations onto the routing network, memoised per coordinate and costing."""

from __future__ import annotations

import json
import logging
from typing import Any

from core import constants
from core.config import settings
from core.errors import UpstreamUnavailableError

from . import http
from .constants import LOCATE_RADIUS_M, LOCATE_SNAP_RADIUS_M, costing_for

logger = logging.getLogger(__name__)

SNAP_CACHE: dict[tuple[int, int, str], tuple[float, float]] = {}

LOCATE_BATCH_MAX = 50


def _cache_key(lat: float, lon: float, costing: str) -> tuple[int, int, str]:
    return (round(lat * 100_000), round(lon * 100_000), costing)


def _locate_batch(
    points: list[tuple[float, float]], costing: str, timeout: float, radius: int
) -> list[tuple[float, float] | None]:
    """One /locate call for the whole batch; None where the engine found no edge."""
    payload = {
        "locations": [{"lat": lat, "lon": lon, "radius": radius} for lat, lon in points],
        "costing": costing,
    }
    try:
        body = http.request_with_retry(
            "GET",
            f"{settings.VALHALLA_URL.rstrip('/')}/locate",
            params={"json": json.dumps(payload, separators=(",", ":"))},
            timeout=timeout,
        )
    except UpstreamUnavailableError:
        return [None] * len(points)
    if isinstance(body, dict):
        body = [body]
    if not isinstance(body, list):
        return [None] * len(points)
    found: list[tuple[float, float] | None] = []
    for entry in body:
        edges = (entry or {}).get("edges") or []
        edge = edges[0] if edges else {}
        if "correlated_lat" in edge and "correlated_lon" in edge:
            found.append((float(edge["correlated_lat"]), float(edge["correlated_lon"])))
        else:
            found.append(None)
    found.extend([None] * (len(points) - len(found)))
    return found


def snap_locations(
    locations: list[dict[str, Any]],
    costing: str = "pedestrian",
    timeout: float | None = None,
    radius: int = LOCATE_SNAP_RADIUS_M,
) -> list[dict[str, Any]]:
    """Snap every location onto the routing network; keeps lat/lon dict shape."""
    costing = costing_for(costing)
    coords = [(float(loc["lat"]), float(loc["lon"])) for loc in locations]
    keys = [_cache_key(lat, lon, costing) for lat, lon in coords]

    pending: dict[tuple[int, int, str], tuple[float, float]] = {}
    for key, point in zip(keys, coords, strict=True):
        if key not in SNAP_CACHE and key not in pending:
            pending[key] = point

    wanted = list(pending)
    for start in range(0, len(wanted), LOCATE_BATCH_MAX):
        chunk = wanted[start : start + LOCATE_BATCH_MAX]
        found = _locate_batch(
            [pending[key] for key in chunk],
            costing,
            timeout or constants.VALHALLA_TIMEOUT_S,
            radius,
        )
        for key, snapped in zip(chunk, found, strict=True):
            SNAP_CACHE[key] = snapped or pending[key]

    return [
        {**loc, "lat": SNAP_CACHE[key][0], "lon": SNAP_CACHE[key][1]}
        for loc, key in zip(locations, keys, strict=True)
    ]


def _snappable_batch(
    locations: list[dict[str, Any]], costing: str, timeout: float | None
) -> list[bool]:
    """Ask /locate once which of these points have an edge on the network."""
    payload = {
        "locations": [{**loc, "radius": LOCATE_RADIUS_M} for loc in locations],
        "costing": costing,
    }
    try:
        body = http.request_with_retry(
            "GET",
            f"{settings.VALHALLA_URL.rstrip('/')}/locate",
            params={"json": json.dumps(payload, separators=(",", ":"))},
            timeout=timeout or constants.VALHALLA_TIMEOUT_S,
        )
    except UpstreamUnavailableError:
        return [True] * len(locations)
    if isinstance(body, dict):
        body = [body]
    if not isinstance(body, list) or not body:
        return [False] * len(locations)
    verdicts = [bool((entry or {}).get("edges")) for entry in body]
    verdicts.extend([False] * (len(locations) - len(verdicts)))
    return verdicts


def drop_unsnappable(
    locations: list[dict[str, Any]], costing: str, timeout: float | None
) -> list[dict[str, Any]]:
    """Keep only the locations /locate can place on the routing network."""
    keep = [
        loc
        for loc, snappable in zip(
            locations, _snappable_batch(locations, costing, timeout), strict=True
        )
        if snappable
    ]
    if len(keep) != len(locations):
        logger.warning(
            "snap: dropped %d of %d location(s) with no edge on the network",
            len(locations) - len(keep),
            len(locations),
        )
    return keep

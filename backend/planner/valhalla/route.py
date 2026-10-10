"""Route and trip decoding plus the public route API."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable

from core import constants
from core.config import settings
from core.errors import UpstreamUnavailable

from . import http, snap
from .constants import _PRECISION, LOCATION_SNAP_RADIUS_M, ROUTE_SNAP_RADII_M
from .matrix import _is_route_failure
from .types import RouteResult, RouteStatus

logger = logging.getLogger(__name__)


def _decode_polyline(encoded: str) -> list[list[float]]:
    coords: list[list[float]] = []
    lat = lon = 0
    i = 0
    while i < len(encoded):
        for axis in range(2):
            shift = result = 0
            while True:
                byte = ord(encoded[i]) - 63
                i += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coords.append([lon / _PRECISION, lat / _PRECISION])
    return coords


def _trip_to_shape(body: dict, language: str | None = None) -> RouteResult:
    """Convert Valhalla trip response to typed RouteResult."""
    if "trip" not in body:
        return RouteResult(
            status=RouteStatus.EMPTY_GEOMETRY,
            shape={},
            summary=None,
            maneuvers=None,
            language=language,
        )
    trip = body["trip"]
    coords: list[list[float]] = []
    maneuvers: list[dict] = []
    for leg in trip.get("legs", []):
        coords.extend(_decode_polyline(leg.get("shape", "")))
        maneuvers.extend(leg.get("maneuvers", []))

    shape = {"type": "LineString", "coordinates": coords}
    summary = trip.get("summary")

    if not coords:
        return RouteResult(
            status=RouteStatus.EMPTY_GEOMETRY,
            shape=shape,
            summary=summary,
            maneuvers=maneuvers,
            language=language,
        )

    return RouteResult(
        status=RouteStatus.USABLE,
        shape=shape,
        summary=summary,
        maneuvers=maneuvers,
        language=language,
    )


def _route_request(
    locations: list[dict],
    costing: str,
    language: str,
    timeout: float | None,
    radius: int,
) -> dict:
    locs = [{**loc, "radius": radius} for loc in locations]
    payload = {
        "costing": costing,
        "locations": locs,
        "units": "kilometers",
        "language": language,
        "alternates": 0,
        "directions_options": {"units": "kilometers"},
    }
    return http.request_with_retry(
        "GET",
        f"{settings.VALHALLA_URL.rstrip('/')}/route",
        params={"json": json.dumps(payload, separators=(",", ":"))},
        timeout=timeout or constants.VALHALLA_TIMEOUT_S,
    )


def optimized_route(
    locations: list[dict],
    costing: str = "pedestrian",
    language: str = "ru",
    timeout: float | None = None,
    retries: int | None = None,
) -> tuple[list[int], dict, dict | None]:
    """Valhalla's own stop ordering: GET /optimized_route.

    Returns (order, shape, summary); order[i] indexes into `locations`.
    """
    snapped = snap.snap_locations(locations, costing, timeout)
    payload = {
        "costing": costing,
        "locations": [
            {**loc, "type": "break", "radius": LOCATION_SNAP_RADIUS_M} for loc in snapped
        ],
        "units": "kilometers",
        "language": language,
        "directions_options": {"units": "kilometers"},
    }
    body = http.request_with_retry(
        "GET",
        f"{settings.VALHALLA_URL.rstrip('/')}/optimized_route",
        params={"json": json.dumps(payload, separators=(",", ":"))},
        timeout=timeout or constants.VALHALLA_TIMEOUT_S,
        retries=retries,
    )
    trip = body.get("trip") or {}
    order: list[int] = []
    for loc in trip.get("locations", []):
        idx = loc.get("original_index")
        if idx is not None and int(idx) not in order:
            order.append(int(idx))
    result = _trip_to_shape(body, language)
    return order, result.shape, result.summary


def route_through(
    locations: Iterable[dict],
    costing: str = "pedestrian",
    language: str = "ru",
    timeout: float | None = None,
) -> RouteResult:
    """Call GET /route. Locations are [{lat, lon, type}], first/last 'break'.

    Unsnappable stops are retried at a wider radius, then dropped.
    """
    locs = [dict(loc) for loc in locations]
    if len(locs) < 2:
        return RouteResult(
            status=RouteStatus.NO_ROUTE_EXISTS,
            shape={},
            summary=None,
            maneuvers=None,
            language=language,
        )
    locs = snap.snap_locations(locs, costing, timeout)

    locs = snap._drop_unsnappable(locs, costing, timeout)
    if len(locs) < 2:
        logger.warning("route: fewer than 2 of the stops can be snapped — no route")
        return RouteResult(
            status=RouteStatus.NO_ROUTE_EXISTS,
            shape={},
            summary=None,
            maneuvers=None,
            language=language,
        )

    last_exc: Exception | None = None
    for radius in ROUTE_SNAP_RADII_M:
        try:
            body = _route_request(locs, costing, language, timeout, radius)
        except UpstreamUnavailable as exc:
            if not _is_route_failure(exc):
                return RouteResult(
                    status=RouteStatus.SERVICE_UNAVAILABLE,
                    shape={},
                    summary=None,
                    maneuvers=None,
                    language=language,
                )
            last_exc = exc
            continue
        return _trip_to_shape(body, language)

    for i in range(len(locs)):
        subset = locs[:i] + locs[i + 1 :]
        if len(subset) < 2:
            break
        try:
            body = _route_request(subset, costing, language, timeout, ROUTE_SNAP_RADII_M[-1])
        except UpstreamUnavailable as exc:
            last_exc = exc
            continue
        logger.warning("route: dropped 1 stop that kept failing, built with %d", len(subset))
        return _trip_to_shape(body, language)

    logger.warning("route: no routable pair among %d stops (%s)", len(locs), last_exc)
    return RouteResult(
        status=RouteStatus.NO_ROUTE_EXISTS,
        shape={},
        summary=None,
        maneuvers=None,
        language=language,
    )

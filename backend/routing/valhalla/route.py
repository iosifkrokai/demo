"""Route and trip decoding plus the public route API."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from core import constants
from core.config import settings
from core.errors import UpstreamUnavailableError

from . import http, snap
from .constants import (
    ALTERNATES_MAX,
    LOCATION_SNAP_RADIUS_M,
    PRECISION,
    ROUTE_SNAP_RADII_M,
    costing_for,
)
from .matrix import is_route_failure
from .types import RouteResult, RouteStatus

logger = logging.getLogger(__name__)


_VARINT_CONTINUE = 0x20


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
                if byte < _VARINT_CONTINUE:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coords.append([lon / PRECISION, lat / PRECISION])
    return coords


def _trip_to_shape(body: dict[str, Any], language: str | None = None) -> RouteResult:
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
    maneuvers: list[dict[str, Any]] = []
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


@dataclass(frozen=True)
class RouteOptions:
    """One route request's engine settings, through the snap radii it retries."""

    costing: str
    language: str
    timeout: float | None
    costing_options: dict[str, Any] | None = None
    alternates: int = 0


def _route_request(
    locations: list[dict[str, Any]],
    options: RouteOptions,
    radius: int,
) -> dict[str, Any]:
    locs = [{**loc, "radius": radius} for loc in locations]
    payload = {
        "costing": options.costing,
        "locations": locs,
        "units": "kilometers",
        "language": options.language,
        "alternates": options.alternates,
        "directions_options": {"units": "kilometers"},
    }
    if options.costing_options:
        payload["costing_options"] = options.costing_options
    return http.request_with_retry(
        "GET",
        f"{settings.VALHALLA_URL.rstrip('/')}/route",
        params={"json": json.dumps(payload, separators=(",", ":"))},
        timeout=options.timeout or constants.VALHALLA_TIMEOUT_S,
    )


def optimized_route(
    locations: list[dict[str, Any]],
    costing: str = "pedestrian",
    language: str = "ru",
    timeout: float | None = None,
    retries: int | None = None,
    costing_options: dict[str, Any] | None = None,
) -> tuple[list[int], dict[str, Any], dict[str, Any] | None]:
    """Valhalla's own stop ordering: GET /optimized_route."""
    costing = costing_for(costing)
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
    if costing_options:
        payload["costing_options"] = costing_options
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
    locations: Iterable[dict[str, Any]],
    costing: str = "pedestrian",
    language: str = "ru",
    timeout: float | None = None,
    costing_options: dict[str, Any] | None = None,
) -> RouteResult:
    """Call GET /route. Locations are [{lat, lon, type}], first/last 'break'."""
    costing = costing_for(costing)
    options = RouteOptions(costing, language, timeout, costing_options)
    locs = [dict(loc) for loc in locations]
    if len(locs) < constants.MIN_LEG_POINTS:
        return RouteResult(
            status=RouteStatus.NO_ROUTE_EXISTS,
            shape={},
            summary=None,
            maneuvers=None,
            language=language,
        )
    locs = snap.snap_locations(locs, costing, timeout)

    locs = snap.drop_unsnappable(locs, costing, timeout)
    if len(locs) < constants.MIN_LEG_POINTS:
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
            body = _route_request(locs, options, radius)
        except UpstreamUnavailableError as exc:
            if not is_route_failure(exc):
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

    best: tuple[float, int, RouteResult] | None = None
    for i in [*range(1, len(locs) - 1), 0, len(locs) - 1]:
        subset = locs[:i] + locs[i + 1 :]
        if len(subset) < constants.MIN_LEG_POINTS:
            continue
        try:
            body = _route_request(subset, options, ROUTE_SNAP_RADII_M[-1])
        except UpstreamUnavailableError as exc:
            last_exc = exc
            continue
        result = _trip_to_shape(body, language)
        seconds = _summary_seconds(result.summary)
        if best is None or seconds < best[0]:
            best = (seconds, i, result)

    if best is not None:
        logger.warning(
            "route: dropped stop %d of %d that kept failing, built with %d",
            best[1] + 1,
            len(locs),
            len(locs) - 1,
        )
        return best[2]

    logger.warning("route: no routable pair among %d stops (%s)", len(locs), last_exc)
    return RouteResult(
        status=RouteStatus.NO_ROUTE_EXISTS,
        shape={},
        summary=None,
        maneuvers=None,
        language=language,
    )


def _summary_seconds(summary: dict[str, Any] | None) -> float:
    """The engine's own time for a trip, when it gave one."""
    if not summary:
        return float("inf")
    seconds = summary.get("time")
    return float(seconds) if isinstance(seconds, int | float) else float("inf")


def _fastest(body: dict[str, Any], language: str | None) -> RouteResult:
    """The quickest of the routes in one answer: the primary and its alternates."""
    result = _trip_to_shape(body, language)
    quickest = _summary_seconds(result.summary)
    for alternate in body.get("alternates") or []:
        candidate = _trip_to_shape({"trip": alternate.get("trip") or {}}, language)
        if candidate.status != RouteStatus.USABLE:
            continue
        seconds = _summary_seconds(candidate.summary)
        if seconds < quickest:
            result, quickest = candidate, seconds
    return result


def best_route(
    locations: Iterable[dict[str, Any]],
    costing: str = "pedestrian",
    language: str = "ru",
    timeout: float | None = None,
    costing_options: dict[str, Any] | None = None,
) -> RouteResult:
    """The quickest route the engine offers for this pair, its alternates included."""
    costing = costing_for(costing)
    options = RouteOptions(costing, language, timeout, costing_options, ALTERNATES_MAX)
    locs = snap.snap_locations([dict(loc) for loc in locations], costing, timeout)
    if len(locs) < constants.MIN_LEG_POINTS:
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
            body = _route_request(locs, options, radius)
        except UpstreamUnavailableError as exc:
            if not is_route_failure(exc):
                return RouteResult(
                    status=RouteStatus.SERVICE_UNAVAILABLE,
                    shape={},
                    summary=None,
                    maneuvers=None,
                    language=language,
                )
            last_exc = exc
            continue
        return _fastest(body, language)

    logger.warning("route: no route for the pair (%s)", last_exc)
    return RouteResult(
        status=RouteStatus.NO_ROUTE_EXISTS,
        shape={},
        summary=None,
        maneuvers=None,
        language=language,
    )

"""Thin Valhalla HTTP client with retries + matrix support.

Two operations:
  - route_through(locations): GET /route → (shape, summary)
  - time_matrix(sources, targets): GET /sources_to_targets → NxM seconds/seconds matrix

The matrix is what the planner uses to pick a better ordering than the
greedy nearest-neighbour heuristic in the original code. With n ≤ 8 a
brute-force over n! permutations is fine and guarantees the optimal order.
"""

from __future__ import annotations

import json
import time as _time
from typing import Iterable

import httpx

from .config import settings
from .errors import UpstreamUnavailable

# Valhalla encodes leg shapes as an encoded polyline (same alphabet and delta
# scheme as Google's) with 1e6 precision, not as "lon,lat lon,lat" pairs.
_PRECISION = 1e6


def _decode_polyline(encoded: str) -> list[list[float]]:
    coords: list[list[float]] = []
    lat = lon = 0
    i = 0
    while i < len(encoded):
        for axis in range(2):  # 0 = latitude, 1 = longitude
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


def _request_with_retry(method: str, url: str, *, params: dict, timeout: float) -> dict:
    """GET with bounded retries on transient failures.

    httpx raises on connect errors / timeouts / 5xx. We catch and retry up to
    VALHALLA_MAX_RETRIES times with linear backoff. The last error is wrapped
    as UpstreamUnavailable so main.py can return 503.
    """
    last_exc: Exception | None = None
    for attempt in range(1, settings.VALHALLA_MAX_RETRIES + 2):  # 1 + retries
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.request(method, url, params=params)
                if r.status_code >= 500:
                    raise httpx.HTTPStatusError("server error", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.HTTPStatusError) as e:
            last_exc = e
            if attempt > settings.VALHALLA_MAX_RETRIES:
                break
            _time.sleep(0.5 * attempt)  # 0.5s, 1.0s between retries
    raise UpstreamUnavailable(f"valhalla {method} {url} failed after retries: {last_exc}")


def ping(timeout: float = 2.0) -> bool:
    """Cheap liveness check: GET /status. Used by /health."""
    try:
        _request_with_retry(
            "GET",
            f"{settings.VALHALLA_URL.rstrip('/')}/status",
            params={},
            timeout=timeout,
        )
        return True
    except Exception:
        return False


def route_through(
    locations: Iterable[dict],
    costing: str = "pedestrian",
    language: str = "ru",
    timeout: float | None = None,
):
    """Call GET /route. Locations are [{lat, lon, type}], first/last 'break'.

    Returns:
        shape   — GeoJSON LineString geometry for the trip (or {} on failure).
        summary — Valhalla trip.summary dict (km, seconds) or None.

    Raises UpstreamUnavailable on persistent network failure.
    """
    payload = {
        "costing": costing,
        "locations": list(locations),
        "units": "kilometers",
        "language": language,
        "alternates": 0,
        "directions_options": {"units": "kilometers"},
    }
    body = _request_with_retry(
        "GET",
        f"{settings.VALHALLA_URL.rstrip('/')}/route",
        params={"json": json.dumps(payload, separators=(",", ":"))},
        timeout=timeout or settings.VALHALLA_TIMEOUT_S,
    )
    if "trip" not in body:
        return {}, None
    trip = body["trip"]
    legs = trip.get("legs", [])
    coords: list[list[float]] = []
    for leg in legs:
        coords.extend(_decode_polyline(leg.get("shape", "")))
    shape = {"type": "LineString", "coordinates": coords}
    return shape, trip.get("summary")


def time_matrix(
    sources: list[dict],
    targets: list[dict],
    costing: str = "pedestrian",
    timeout: float | None = None,
) -> list[list[float]]:
    """GET /sources_to_targets → symmetric-ish time matrix in seconds.

    sources/targets are [{lat, lon}] objects. The matrix has shape
    [len(sources)][len(targets)]; cell [i][j] is the walk time from
    sources[i] to targets[j] in seconds. Diagonal of a square matrix
    (sources == targets) is 0.

    A small matrix (≤ 50 cells) is one HTTP call to Valhalla.
    """
    payload = {
        "costing": costing,
        "sources": sources,
        "targets": targets,
        "units": "kilometers",
    }
    body = _request_with_retry(
        "GET",
        f"{settings.VALHALLA_URL.rstrip('/')}/sources_to_targets",
        params={"json": json.dumps(payload, separators=(",", ":"))},
        timeout=timeout or settings.VALHALLA_TIMEOUT_S,
    )
    # The response is { "sources_to_targets": [[{ "time": s, "distance": km }, ...], ...] }
    rows = body.get("sources_to_targets") or []
    matrix: list[list[float]] = []
    for row in rows:
        matrix.append([float(cell.get("time", 0.0)) for cell in row])
    return matrix

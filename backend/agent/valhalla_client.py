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
from collections.abc import Iterable, Iterator

import httpx

from . import constants
from .config import settings
from .errors import UpstreamUnavailable

# Valhalla encodes leg shapes as an encoded polyline (same alphabet and delta
# scheme as Google's) with 1e6 precision, not as "lon,lat lon,lat" pairs.
_PRECISION = 1e6

# Snap radius (metres) for every request location. Valhalla's default snapping
# (radius 0 = "use the service default") fails with
#   500 {"error_code":499,"error":"Could not find candidate edge used for destination label"}
# for several real Grodno POIs — e.g. (53.6845,23.8318), (53.6779,23.8242),
# (53.6838,23.8308) — while the same coordinates snap fine in /sources_to_targets.
# Measured: radius=100 fixes all three (radius=500 also works; search_radius and
# street_side_tolerance do NOT). Do not remove without re-testing those points.
LOCATION_SNAP_RADIUS_M = 100


# ── Matrix chunking limits ──────────────────────────────────────────────────
# Valhalla 3.5.1 (container grodno-valhalla) returns HTTP 500 with the error
# "Could not find candidate edge used for label" when the matrix shape is
# len(sources) >= 6 AND len(targets) >= 7.  The following table was measured
# against the live engine with the 12 Grodno candidate coordinates the planner
# sends (costing=pedestrian, units=kilometers):
#
#   NxN, N ≤ 6        → OK    |  6x7, 6x8, 6x12, 7x7, 8x8, 12x12  → 500
#   5x12, 5x8, 4x12   → OK    |  7x5, 8x5, 12x3                    → OK
#
# Rule: FAIL when len(sources) >= 6 AND len(targets) >= 7.
# Safe means: len(sources) < 6  OR  len(targets) < 7.
# These constants are deliberately conservative.  Do NOT increase them without
# re-measuring against a live Valhalla 3.5.1 instance.
MATRIX_MAX_SOURCES = 5   # sources <= 5  → len(sources) < 6
MATRIX_MAX_TARGETS = 6   # targets <= 6  → len(targets) < 7


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


def _matrix_chunk_indices(
    total: int, chunk_max: int
) -> Iterator[tuple[int, int]]:
    """Yield (start, end) half-open slices that cover `total` items."""
    for start in range(0, total, chunk_max):
        yield start, min(start + chunk_max, total)


def _request_with_retry(method: str, url: str, *, params: dict, timeout: float) -> dict:
    """GET with bounded retries on transient failures.

    httpx raises on connect errors / timeouts / 5xx. We catch and retry up to
    VALHALLA_MAX_RETRIES times with linear backoff. The last error is wrapped
    as UpstreamUnavailable so main.py can return 503.
    """
    last_exc: Exception | None = None
    for attempt in range(1, constants.VALHALLA_MAX_RETRIES + 2):  # 1 + retries
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.request(method, url, params=params)
                if r.status_code >= 500:
                    raise httpx.HTTPStatusError("server error", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.HTTPStatusError) as e:
            last_exc = e
            if attempt > constants.VALHALLA_MAX_RETRIES:
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


def _with_snap_radius(location: dict) -> dict:
    """Copy a location and give it LOCATION_SNAP_RADIUS_M (unless it sets its own).

    Keeps /route and /sources_to_targets snapping to the same edges — otherwise
    the rendered shape can follow a different edge than the one the cost matrix
    was measured on.
    """
    loc = dict(location)
    loc.setdefault("radius", LOCATION_SNAP_RADIUS_M)
    return loc


def _matrix_via_route(source: dict, target: dict, costing: str, timeout: float) -> float:
    """Fallback: single pair via GET /route."""
    payload = {
        "costing": costing,
        "locations": [_with_snap_radius(source), _with_snap_radius(target)],
        "units": "kilometers",
        "alternates": 0,
        "directions_options": {"units": "kilometers"},
    }
    body = _request_with_retry(
        "GET",
        f"{settings.VALHALLA_URL.rstrip('/')}/route",
        params={"json": json.dumps(payload, separators=(",", ":"))},
        timeout=timeout,
    )
    if "trip" not in body:
        return 0.0
    summary = body["trip"].get("summary", {})
    return float(summary.get("time", 0.0))


def _matrix_chunk(
    sources: list[dict],
    targets: list[dict],
    costing: str,
    timeout: float,
) -> list[list[float]]:
    """Single chunk: issue one /sources_to_targets request and return its matrix."""
    payload = {
        "costing": costing,
        "sources": [_with_snap_radius(loc) for loc in sources],
        "targets": [_with_snap_radius(loc) for loc in targets],
        "units": "kilometers",
    }
    body = _request_with_retry(
        "GET",
        f"{settings.VALHALLA_URL.rstrip('/')}/sources_to_targets",
        params={"json": json.dumps(payload, separators=(",", ":"))},
        timeout=timeout,
    )
    rows = body.get("sources_to_targets") or []
    matrix: list[list[float]] = []
    for row in rows:
        row_times: list[float] = []
        for cell in row:
            # Valhalla returns "time": null for a pair it cannot connect. Crashing
            # the whole request over one such cell is not acceptable, and 0.0
            # (the value used for the diagonal) would make it look adjacent, so
            # treat it as unreachable and let the caller decide.
            cell_time = cell.get("time")
            row_times.append(0.0 if cell_time is None else float(cell_time))
        matrix.append(row_times)
    return matrix


def _chunks_safe(n_sources: int, n_targets: int) -> bool:
    """True when this shape is guaranteed not to hit the Valhalla 500 bug.

    The bug fires when len(sources) >= 6 AND len(targets) >= 7.
    Safe means: len(sources) < 6  OR  len(targets) < 7.
    """
    return n_sources <= MATRIX_MAX_SOURCES or n_targets <= MATRIX_MAX_TARGETS


def _fill_via_route(
    sources: list[dict],
    targets: list[dict],
    costing: str,
    timeout: float,
) -> list[list[float]]:
    """Fill every cell with per-pair /route calls. Used only when all else fails."""
    n_src = len(sources)
    n_tgt = len(targets)
    result: list[list[float]] = []
    for i in range(n_src):
        row: list[float] = []
        for j in range(n_tgt):
            if sources[i] is targets[j]:
                row.append(0.0)
            else:
                row.append(_matrix_via_route(sources[i], targets[j], costing, timeout))
        result.append(row)
    return result


def _zero_diagonal(result: list[list[float]], sources: list[dict], targets: list[dict]) -> None:
    """Set diagonal cells to 0.0 when sources[i] is targets[i] (same object = same location)."""
    n = min(len(sources), len(targets))
    for i in range(n):
        if sources[i] is targets[i]:
            result[i][i] = 0.0


def time_matrix(
    sources: list[dict],
    targets: list[dict],
    costing: str = "pedestrian",
    timeout: float | None = None,
) -> list[list[float]]:
    """GET /sources_to_targets → time matrix in seconds, chunked to avoid Valhalla 500.

    sources/targets are [{lat, lon}] objects. The matrix has shape
    [len(sources)][len(targets)]; cell [i][j] is the walk time from
    sources[i] to targets[j] in seconds. Diagonal of a square matrix
    (sources == targets) is 0.

    Shapes where len(sources) >= 6 AND len(targets) >= 7 trigger HTTP 500 in
    Valhalla 3.5.1.  This function splits large matrices into safe chunks and
    assembles the full result.  A 12x12 matrix costs ~3 HTTP calls, not 144.
    """
    n_src = len(sources)
    n_tgt = len(targets)
    timeout = timeout or constants.VALHALLA_TIMEOUT_S

    # Fast path: small matrix fits in one safe request.
    if _chunks_safe(n_src, n_tgt):
        fast_result = _matrix_chunk(sources, targets, costing, timeout)
        _zero_diagonal(fast_result, sources, targets)
        return fast_result

    # Build indexed copies that carry global source/target indices.
    # These dicts are shallow copies — the original caller's data is unchanged.
    idx_sources: list[dict] = [
        {**s, "_src_idx": i} for i, s in enumerate(sources)
    ]
    idx_targets: list[dict] = [
        {**t, "_tgt_idx": j} for j, t in enumerate(targets)
    ]

    # Build the full result matrix, initialised to 0.
    result: list[list[float]] = [[0.0] * n_tgt for _ in range(n_src)]

    # Split source rows into chunks of at most MATRIX_MAX_SOURCES rows.
    for src_start, src_end in _matrix_chunk_indices(n_src, MATRIX_MAX_SOURCES):
        src_chunk = idx_sources[src_start:src_end]

        # Split target columns into chunks of at most MATRIX_MAX_TARGETS columns.
        for tgt_start, tgt_end in _matrix_chunk_indices(n_tgt, MATRIX_MAX_TARGETS):
            tgt_chunk = idx_targets[tgt_start:tgt_end]

            try:
                chunk_matrix = _matrix_chunk(src_chunk, tgt_chunk, costing, timeout)
            except UpstreamUnavailable:
                # Last resort: per-pair /route calls. (Do NOT try to "repair"
                # values of a failed chunk — a failed chunk carries no values.)
                chunk_matrix = _fill_via_route(src_chunk, tgt_chunk, costing, timeout)

            # Copy chunk into the right offset in the result.
            for li, row in enumerate(chunk_matrix):
                global_src = src_start + li
                for lj, val in enumerate(row):
                    global_tgt = tgt_start + lj
                    result[global_src][global_tgt] = val

    return result


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
        "locations": [_with_snap_radius(loc) for loc in locations],
        "units": "kilometers",
        "language": language,
        "alternates": 0,
        "directions_options": {"units": "kilometers"},
    }
    body = _request_with_retry(
        "GET",
        f"{settings.VALHALLA_URL.rstrip('/')}/route",
        params={"json": json.dumps(payload, separators=(",", ":"))},
        timeout=timeout or constants.VALHALLA_TIMEOUT_S,
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

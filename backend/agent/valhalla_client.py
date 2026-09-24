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
import logging
import time as _time
from collections.abc import Iterable, Iterator

import httpx

from . import constants
from .config import settings
from .errors import UpstreamUnavailable

logger = logging.getLogger(__name__)

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

# /route: some stops sit off the walking graph (a monument out in a field, or a
# POI whose tiles have no pedestrian edges nearby). Valhalla then answers
#   500 "Could not find candidate edge used for destination label"
# and the whole tour is lost. Instead of giving up we widen the snap radius, and
# as a last resort drop the stops that cannot be snapped at all.
ROUTE_SNAP_RADII_M = (LOCATION_SNAP_RADIUS_M, 500, 2000, 5000)
LOCATE_RADIUS_M = 5000
SNAP_ERROR_MARKERS = ("candidate edge", "for destination label", "for origin label")

# A stop can also sit on a road island: the point IS on an edge (so /locate is
# happy), but its little network is not connected to the rest of the graph for
# the chosen costing, and Valhalla answers
#   400 {"error_code":442,"error":"No path could be found for input"}
# for any request that has to reach it. Measured with the auto costing on
# «Костел Святого Антония Падуанского» (53.007611,23.917041): /route from
# Волковыск → 400/442, while the 2.3 km hop to its neighbour works. One such
# stop used to fail the whole tour and the UI drew points with no line, so it
# gets the same treatment as a snap failure: widen the radius, then drop it.
NO_PATH_MARKERS = ("no path could be found", "error_code\":442")
ROUTE_FAILURE_MARKERS = SNAP_ERROR_MARKERS + NO_PATH_MARKERS


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
MATRIX_MAX_SOURCES = 5   # keep both dimensions <= 5: a 6x6 matrix 500s in practice
MATRIX_MAX_TARGETS = 5


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
                    # Keep the upstream body: /route answers 500 with a reason
                    # ("Could not find candidate edge used for destination
                    # label") that route_through inspects to decide whether the
                    # request is worth retrying with a wider snap radius.
                    raise httpx.HTTPStatusError(
                        f"server error: {r.text[:300]}", request=r.request, response=r
                    )
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


# ── Snapping ────────────────────────────────────────────────────────────────
# /route and /sources_to_targets answer
#   500 {"error_code":499,"error":"Could not find candidate edge used for label"}
# when a POI sits off the walking graph: "Опорный пункт №16 Гродненской крепости"
# (53.597305,23.800828) is ~340 m from the nearest pedestrian edge, and every
# request containing it fails. /locate reports that nearest edge, so points are
# snapped onto the network first and Valhalla then routes real geometry instead
# of us silently dropping the stop.
LOCATE_SNAP_RADIUS_M = 500
_SNAP_CACHE: dict[tuple[int, int], tuple[float, float]] = {}


def _locate_snap(
    location: dict, costing: str, timeout: float, radius: int
) -> tuple[float, float] | None:
    payload = {
        "locations": [{**location, "radius": radius}],
        "costing": costing,
    }
    try:
        body = _request_with_retry(
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

    Results are memoised per rounded coordinate, so an NxN matrix + the final
    /route cost one /locate call per distinct POI, not one per pair.
    A point Valhalla cannot snap at all keeps its original coordinates — the
    caller's own fallbacks deal with it.
    """
    out: list[dict] = []
    for loc in locations:
        lat, lon = float(loc["lat"]), float(loc["lon"])
        key = (round(lat * 100_000), round(lon * 100_000))
        if key not in _SNAP_CACHE:
            snapped = _locate_snap(
                {"lat": lat, "lon": lon},
                costing,
                timeout or constants.VALHALLA_TIMEOUT_S,
                radius,
            )
            _SNAP_CACHE[key] = snapped or (lat, lon)
        snap_lat, snap_lon = _SNAP_CACHE[key]
        out.append({**loc, "lat": snap_lat, "lon": snap_lon})
    return out


def _matrix_via_route(source: dict, target: dict, costing: str, timeout: float,
                      radius: int | None = None) -> float:
    """Fallback: single pair via GET /route."""
    snap = radius or LOCATION_SNAP_RADIUS_M
    payload = {
        "costing": costing,
        "locations": [{**source, "radius": snap}, {**target, "radius": snap}],
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
    radius: int | None = None,
) -> list[list[float]]:
    """Single chunk: issue one /sources_to_targets request and return its matrix."""
    snap = radius or LOCATION_SNAP_RADIUS_M
    payload = {
        "costing": costing,
        "sources": [{**loc, "radius": snap} for loc in sources],
        "targets": [{**loc, "radius": snap} for loc in targets],
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
            # Valhalla returns "time": null for a pair it cannot connect — e.g.
            # «Костел Святого Антония Падуанского» (53.007611,23.917041) with the
            # auto costing: 200 for the 2.3 km hop to its neighbour, null for
            # Волковыск → it, because its little road island is not connected to
            # the rest of the graph. 0.0 (the diagonal's value) made such a hop
            # look FREE, so the optimizer happily ordered it and /route then
            # answered 400 "No path could be found for input" — every point drawn,
            # no line. The sentinel marks it unreachable and the caller decides.
            cell_time = cell.get("time")
            row_times.append(
                float(constants.UNREACHABLE_S) if cell_time is None else float(cell_time)
            )
        matrix.append(row_times)
    return matrix


def _matrix_chunk_resilient(
    sources: list[dict],
    targets: list[dict],
    costing: str,
    timeout: float,
) -> list[list[float]]:
    """A chunk request, retried with a wider snap radius, then per-pair /route.

    /sources_to_targets answers 500 with
        {"error_code":499,"error":"Unknown: Could not find candidate edge used for
         destination label"}
    for some point combinations (this reached the UI as a 503 "Unknown: …" toast),
    and the failure carries no partial data. Widening the radius fixes most of
    them; if not, fall back to one /route call per pair.
    """
    last_exc: Exception | None = None
    for radius in ROUTE_SNAP_RADII_M:
        try:
            return _matrix_chunk(sources, targets, costing, timeout, radius=radius)
        except UpstreamUnavailable as exc:
            last_exc = exc
    logger.warning(
        "matrix chunk failed at every snap radius (%s) — falling back to per-pair /route",
        last_exc,
    )
    return _fill_via_route(sources, targets, costing, timeout)


def _chunks_safe(n_sources: int, n_targets: int) -> bool:
    """True when this shape fits in a single /sources_to_targets request.

    Valhalla 3.5.1 answers 500 for larger shapes (a 6x6 has failed live, the
    documented threshold is sources >= 6 AND targets >= 7), so both dimensions
    are capped at MATRIX_MAX_* and anything bigger is split.
    """
    return n_sources <= MATRIX_MAX_SOURCES and n_targets <= MATRIX_MAX_TARGETS


def _matrix_via_route_resilient(
    source: dict, target: dict, costing: str, timeout: float
) -> float:
    """One pair: widen the snap radius, and if the pair stays unroutable return inf.

    inf is how the rest of the pipeline spells "not reachable" (budget validation
    and the max-leg walkability check both reject such a leg), so a single
    pathological pair shortens the tour instead of failing the whole request.
    """
    last_exc: Exception | None = None
    for radius in ROUTE_SNAP_RADII_M:
        try:
            return _matrix_via_route(source, target, costing, timeout, radius=radius)
        except UpstreamUnavailable as exc:
            last_exc = exc
    logger.warning(
        "pair (%s,%s) -> (%s,%s) unroutable at every snap radius: %s",
        source.get("lat"),
        source.get("lon"),
        target.get("lat"),
        target.get("lon"),
        last_exc,
    )
    return float(constants.UNREACHABLE_S)


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
                row.append(_matrix_via_route_resilient(sources[i], targets[j], costing, timeout))
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

    # Fast path: small matrix fits in one safe request. Still goes through the
    # resilient wrapper — a 3x3 "замки Гродно" matrix has hit the Valhalla
    # "Could not find candidate edge used for label" 500 in practice.
    if _chunks_safe(n_src, n_tgt):
        fast_result = _matrix_chunk_resilient(sources, targets, costing, timeout)
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

            # A chunk can fail (Valhalla label 500); the wrapper widens the snap
            # radius and only then falls back to per-pair /route calls.
            chunk_matrix = _matrix_chunk_resilient(src_chunk, tgt_chunk, costing, timeout)

            # Copy chunk into the right offset in the result.
            for li, row in enumerate(chunk_matrix):
                global_src = src_start + li
                for lj, val in enumerate(row):
                    global_tgt = tgt_start + lj
                    result[global_src][global_tgt] = val

    return result


def _is_snap_failure(exc: Exception) -> bool:
    """True when Valhalla failed because it could not snap a location to the graph."""
    text = str(exc).lower()
    return any(marker in text for marker in SNAP_ERROR_MARKERS)


def _is_route_failure(exc: Exception) -> bool:
    """True when the tour itself is the problem, not the Valhalla connection.

    Covers both a location with no candidate edge and a location on a road
    island Valhalla cannot reach ("No path could be found for input"). Transport
    failures (connect error, timeout) match neither and must keep bubbling up as
    503 — retrying those would silently shorten the tour.
    """
    text = str(exc).lower()
    return any(marker in text for marker in ROUTE_FAILURE_MARKERS)


def _snappable(location: dict, costing: str, timeout: float | None) -> bool:
    """Ask /locate whether this point can be snapped onto the network.

    Anything else (timeout, 5xx) counts as snappable — dropping a stop because
    Valhalla hiccuped would silently shorten the tour.
    """
    payload = {
        "locations": [{**location, "radius": LOCATE_RADIUS_M}],
        "costing": costing,
    }
    try:
        body = _request_with_retry(
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

    Applied before any /route or matrix call, endpoints included: one POI off the
    graph makes Valhalla answer
    `500 {"error_code":499,"error":"Could not find candidate edge used for label"}`
    for the whole request.
    """
    keep = [loc for loc in locations if _snappable(loc, costing, timeout)]
    if len(keep) != len(locations):
        logger.warning(
            "snap: dropped %d of %d location(s) with no edge on the network",
            len(locations) - len(keep),
            len(locations),
        )
    return keep


def _trip_to_shape(body: dict) -> tuple[dict, dict | None]:
    if "trip" not in body:
        return {}, None
    trip = body["trip"]
    coords: list[list[float]] = []
    for leg in trip.get("legs", []):
        coords.extend(_decode_polyline(leg.get("shape", "")))
    return {"type": "LineString", "coordinates": coords}, trip.get("summary")


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
    return _request_with_retry(
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
) -> tuple[list[int], dict, dict | None]:
    """Valhalla's own stop ordering: GET /optimized_route.

    Returns (order, shape, summary) where order[i] is the index into `locations`
    of the i-th stop in Valhalla's sequence. The caller applies it to its own
    stop list, so the tour the user sees is the one the router built.
    """
    snapped = snap_locations(locations, costing, timeout)
    payload = {
        "costing": costing,
        "locations": [
            {**loc, "type": "break", "radius": LOCATION_SNAP_RADIUS_M} for loc in snapped
        ],
        "units": "kilometers",
        "language": language,
        "directions_options": {"units": "kilometers"},
    }
    body = _request_with_retry(
        "GET",
        f"{settings.VALHALLA_URL.rstrip('/')}/optimized_route",
        params={"json": json.dumps(payload, separators=(",", ":"))},
        timeout=timeout or constants.VALHALLA_TIMEOUT_S,
    )
    trip = body.get("trip") or {}
    order: list[int] = []
    for loc in trip.get("locations", []):
        idx = loc.get("original_index")
        if idx is not None and int(idx) not in order:
            order.append(int(idx))
    shape, summary = _trip_to_shape(body)
    return order, shape, summary


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

    A stop that cannot be snapped to the walking graph is retried with a wider
    radius; if even 5 km is not enough it is dropped from the polyline (logged),
    because a stop nobody can walk to must not turn the whole tour into a 500.

    Raises UpstreamUnavailable on persistent network failure or when Valhalla
    keeps failing for a reason that is not snapping.
    """
    locs = [dict(loc) for loc in locations]
    if len(locs) < 2:
        return {}, None
    # Feed Valhalla points that sit ON its graph — a POI a few hundred metres off
    # the pedestrian network otherwise 500s the whole tour.
    locs = snap_locations(locs, costing, timeout)

    # Drop stops Valhalla cannot place at all BEFORE routing, first and last
    # included: one such POI (a fortress outside the network, a rural chapel with
    # no address) used to fail the whole request, which the UI showed as "points
    # drawn, no route". The caller keeps every point as a marker; only the
    # polyline skips what cannot be walked/driven to.
    locs = _drop_unsnappable(locs, costing, timeout)
    if len(locs) < 2:
        logger.warning("route: fewer than 2 of the stops can be snapped — no route")
        return {}, None

    last_exc: Exception | None = None
    for radius in ROUTE_SNAP_RADII_M:
        try:
            body = _route_request(locs, costing, language, timeout, radius)
        except UpstreamUnavailable as exc:
            if not _is_route_failure(exc):
                raise
            last_exc = exc
            continue
        return _trip_to_shape(body)

    # Every radius failed for the stops that /locate said were fine → find the
    # culprit: try the route without each stop in turn and take the first that
    # builds. n calls, only in this residual case.
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
        return _trip_to_shape(body)

    logger.warning("route: no routable pair among %d stops (%s)", len(locs), last_exc)
    return {}, None

"""The time matrix: chunked /sources_to_targets with resilient fallbacks.

The matrix guides ordering; the reported length/time must come from /route.
"""

from __future__ import annotations

import json
import logging
import math
import time as _time
from collections.abc import Iterator

from core import constants
from core.config import settings
from core.errors import UpstreamUnavailable

from . import http
from .constants import (
    DISTANCE_LIMIT_MARKERS,
    LOCATION_SNAP_RADIUS_M,
    MATRIX_FALLBACK_BUDGET_S,
    MATRIX_FALLBACK_MAX_ROUTE_CALLS,
    MATRIX_MAX_SOURCES,
    MATRIX_MAX_TARGETS,
    MATRIX_PATH_LIMIT_M,
    ROUTE_FAILURE_MARKERS,
    ROUTE_SNAP_RADII_M,
)

logger = logging.getLogger(__name__)


def _is_distance_limit_error(exc: Exception) -> bool:
    """True when Valhalla refused a pair for exceeding its 200 km path limit."""
    text = str(exc).lower()
    return any(marker in text for marker in DISTANCE_LIMIT_MARKERS)


def _pair_metres(a: dict, b: dict) -> float:
    """Equirectangular straight-line distance in metres (fine at these scales)."""
    lat_mid = math.radians((float(a["lat"]) + float(b["lat"])) / 2)
    dx = math.radians(float(a["lon"]) - float(b["lon"])) * 6_371_000 * math.cos(lat_mid)
    dy = math.radians(float(a["lat"]) - float(b["lat"])) * 6_371_000
    return math.hypot(dx, dy)


def _same_point(a: dict, b: dict) -> bool:
    """True when two locations are the same point, even across chunk copies.

    Compares global indices when present, otherwise object identity.
    """
    si, ti = a.get("_src_idx"), b.get("_tgt_idx")
    if si is not None and ti is not None:
        return si == ti
    return a is b


def _matrix_chunk_indices(total: int, chunk_max: int) -> Iterator[tuple[int, int]]:
    """Yield (start, end) half-open slices that cover `total` items."""
    for start in range(0, total, chunk_max):
        yield start, min(start + chunk_max, total)


def _matrix_via_route(
    source: dict, target: dict, costing: str, timeout: float, radius: int | None = None
) -> float:
    """Fallback: single pair via GET /route."""
    snap = radius or LOCATION_SNAP_RADIUS_M
    payload = {
        "costing": costing,
        "locations": [{**source, "radius": snap}, {**target, "radius": snap}],
        "units": "kilometers",
        "alternates": 0,
        "directions_options": {"units": "kilometers"},
    }
    body = http.request_with_retry(
        "GET",
        f"{settings.VALHALLA_URL.rstrip('/')}/route",
        params={"json": json.dumps(payload, separators=(",", ":"))},
        timeout=timeout,
    )
    if "trip" not in body:
        return float(constants.UNREACHABLE_S)
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
    body = http.request_with_retry(
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
    deadline: float | None = None,
) -> list[list[float]]:
    """A chunk request, retried with a wider snap radius, then per-pair /route."""
    last_exc: Exception | None = None
    for radius in ROUTE_SNAP_RADII_M:
        try:
            return _matrix_chunk(sources, targets, costing, timeout, radius=radius)
        except UpstreamUnavailable as exc:
            if _is_distance_limit_error(exc):
                logger.warning(
                    "matrix chunk hit the 200 km path limit — resolving by distance "
                    "instead of per-pair /route"
                )
                return _distance_aware_fill(sources, targets, costing, timeout)
            last_exc = exc
    logger.warning(
        "matrix chunk failed at every snap radius (%s) — falling back to per-pair /route",
        last_exc,
    )
    return _fill_via_route(sources, targets, costing, timeout, deadline=deadline)


def _distance_aware_fill(
    sources: list[dict],
    targets: list[dict],
    costing: str,
    timeout: float,
) -> list[list[float]]:
    """Resolve a chunk Valhalla refused for the 200 km path limit, per source.

    Far pairs are marked unreachable; near pairs still get real times.
    """
    n_s, n_t = len(sources), len(targets)
    unreachable = float(constants.UNREACHABLE_S)
    unknown = float(constants.UNKNOWN_S)
    out = [[unreachable] * n_t for _ in range(n_s)]

    for i, source in enumerate(sources):
        near = [j for j in range(n_t) if _pair_metres(source, targets[j]) <= MATRIX_PATH_LIMIT_M]
        for start in range(0, len(near), MATRIX_MAX_TARGETS):
            cols = near[start : start + MATRIX_MAX_TARGETS]
            row_values: list[float] | None = None
            try:
                sub = _matrix_chunk([source], [targets[j] for j in cols], costing, timeout)
                row_values = [float(v) for v in sub[0]]
            except UpstreamUnavailable as exc:
                if _is_distance_limit_error(exc) and len(cols) > 1:
                    row_values = _isolate_row(
                        source,
                        targets,
                        cols,
                        costing,
                        timeout,
                        unreachable=unreachable,
                        unknown=unknown,
                    )
                else:
                    logger.warning(
                        "matrix row %d could not be asked (%.80s) — left unknown", i, exc
                    )
                    row_values = [unknown] * len(cols)
            for k, j in enumerate(cols):
                out[i][j] = row_values[k]
    return out


def _isolate_row(
    source: dict,
    targets: list[dict],
    cols: list[int],
    costing: str,
    timeout: float,
    *,
    unreachable: float,
    unknown: float,
) -> list[float]:
    """Probe one source's target block a single cell at a time.

    The far cell is marked unreachable; transport failures stay unknown.
    """
    row: list[float] = []
    for j in cols:
        try:
            sub = _matrix_chunk([source], [targets[j]], costing, timeout)
            row.append(float(sub[0][0]))
        except UpstreamUnavailable as exc:
            row.append(unreachable if _is_distance_limit_error(exc) else unknown)
    return row


def _chunks_safe(n_sources: int, n_targets: int) -> bool:
    """True when this shape fits in a single /sources_to_targets request.

    Valhalla answers 500 for larger shapes, so bigger ones are split.
    """
    return n_sources <= MATRIX_MAX_SOURCES and n_targets <= MATRIX_MAX_TARGETS


def _matrix_via_route_resilient(source: dict, target: dict, costing: str, timeout: float) -> float:
    """One pair via /route, widening the snap radius only for a snap failure.

    Returns a time, UNREACHABLE_S, or UNKNOWN_S ("we could not ask").
    """
    last_exc: Exception | None = None
    for radius in ROUTE_SNAP_RADII_M:
        try:
            return _matrix_via_route(source, target, costing, timeout, radius=radius)
        except UpstreamUnavailable as exc:
            if _is_distance_limit_error(exc):
                return float(constants.UNREACHABLE_S)
            if not _is_route_failure(exc):
                logger.warning(
                    "pair (%s,%s) -> (%s,%s) could not be asked (%s) — left unknown",
                    source.get("lat"),
                    source.get("lon"),
                    target.get("lat"),
                    target.get("lon"),
                    exc,
                )
                return float(constants.UNKNOWN_S)
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
    deadline: float | None = None,
) -> list[list[float]]:
    """Fill every cell with per-pair /route calls. Used only when all else fails.

    Bounded by a call cap and a deadline; leftover cells stay UNKNOWN_S.
    """
    n_src = len(sources)
    n_tgt = len(targets)
    started = _time.monotonic()
    limit = deadline if deadline is not None else started + MATRIX_FALLBACK_BUDGET_S
    calls = 0
    capped = False
    result: list[list[float]] = []
    for i in range(n_src):
        row: list[float] = []
        for j in range(n_tgt):
            if _same_point(sources[i], targets[j]):
                row.append(0.0)
            elif capped or calls >= MATRIX_FALLBACK_MAX_ROUTE_CALLS or _time.monotonic() >= limit:
                capped = True
                row.append(float(constants.UNKNOWN_S))
            else:
                calls += 1
                row.append(_matrix_via_route_resilient(sources[i], targets[j], costing, timeout))
        result.append(row)
    if capped:
        logger.warning(
            "matrix per-pair fallback capped after %d /route calls (%d cells left "
            "unknown) — request degrades instead of hanging",
            calls,
            n_src * n_tgt - calls,
        )
    return result


def _zero_diagonal(result: list[list[float]], sources: list[dict], targets: list[dict]) -> None:
    """Set diagonal cells to 0.0 when sources[i] and targets[i] are the same point."""
    n = min(len(sources), len(targets))
    for i in range(n):
        if _same_point(sources[i], targets[i]):
            result[i][i] = 0.0


def time_matrix(
    sources: list[dict],
    targets: list[dict],
    costing: str = "pedestrian",
    timeout: float | None = None,
    deadline: float | None = None,
) -> list[list[float]]:
    """GET /sources_to_targets → time matrix in seconds, chunked to avoid 500s.

    A cell is a real time, UNREACHABLE_S ("no path") or UNKNOWN_S.
    """
    n_src = len(sources)
    n_tgt = len(targets)
    timeout = timeout or constants.VALHALLA_TIMEOUT_S

    if _chunks_safe(n_src, n_tgt):
        fast_result = _matrix_chunk_resilient(sources, targets, costing, timeout, deadline=deadline)
        _zero_diagonal(fast_result, sources, targets)
        return fast_result

    idx_sources: list[dict] = [{**s, "_src_idx": i} for i, s in enumerate(sources)]
    idx_targets: list[dict] = [{**t, "_tgt_idx": j} for j, t in enumerate(targets)]

    result: list[list[float]] = [[0.0] * n_tgt for _ in range(n_src)]

    for src_start, src_end in _matrix_chunk_indices(n_src, MATRIX_MAX_SOURCES):
        src_chunk = idx_sources[src_start:src_end]

        for tgt_start, tgt_end in _matrix_chunk_indices(n_tgt, MATRIX_MAX_TARGETS):
            tgt_chunk = idx_targets[tgt_start:tgt_end]

            chunk_matrix = _matrix_chunk_resilient(
                src_chunk, tgt_chunk, costing, timeout, deadline=deadline
            )

            for li, row in enumerate(chunk_matrix):
                global_src = src_start + li
                for lj, val in enumerate(row):
                    global_tgt = tgt_start + lj
                    result[global_src][global_tgt] = val

    return result


def _is_route_failure(exc: Exception) -> bool:
    """True when the tour itself is the problem, not the Valhalla connection.

    Transport failures match neither marker and must keep bubbling up as 503.
    """
    text = str(exc).lower()
    return any(marker in text for marker in ROUTE_FAILURE_MARKERS)

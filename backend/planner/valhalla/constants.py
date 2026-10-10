"""Numeric limits and marker tuples for the Valhalla client."""

from __future__ import annotations

_PRECISION = 1e6

LOCATION_SNAP_RADIUS_M = 100

ROUTE_SNAP_RADII_M = (LOCATION_SNAP_RADIUS_M, 500, 2000, 5000)
LOCATE_RADIUS_M = 5000
LOCATE_SNAP_RADIUS_M = 500
SNAP_ERROR_MARKERS = ("candidate edge", "for destination label", "for origin label")

NO_PATH_MARKERS = ("no path could be found", "error_code\":442")
ROUTE_FAILURE_MARKERS = SNAP_ERROR_MARKERS + NO_PATH_MARKERS

MATRIX_PATH_LIMIT_M = 200_000
DISTANCE_LIMIT_MARKERS = ("error_code\":154", "max distance limit")

MATRIX_FALLBACK_MAX_ROUTE_CALLS = 60
MATRIX_FALLBACK_BUDGET_S = 30.0
MATRIX_MAX_SOURCES = 5
MATRIX_MAX_TARGETS = 5

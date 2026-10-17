"""Numeric limits and marker tuples for the Valhalla client."""

from __future__ import annotations

from typing import Any

from core import constants

PRECISION = 1e6

LOCATION_SNAP_RADIUS_M = 100

ROUTE_SNAP_RADII_M = (LOCATION_SNAP_RADIUS_M, 500, 2000, 5000)

MATRIX_SNAP_RADII_M: tuple[int | None, ...] = (None, *ROUTE_SNAP_RADII_M)
LOCATE_RADIUS_M = 5000
LOCATE_SNAP_RADIUS_M = 500
SNAP_ERROR_MARKERS = ("candidate edge", "for destination label", "for origin label")

NO_PATH_MARKERS = ("no path could be found", 'error_code":442')
ROUTE_FAILURE_MARKERS = SNAP_ERROR_MARKERS + NO_PATH_MARKERS

MATRIX_PATH_LIMIT_M = 200_000
DISTANCE_LIMIT_MARKERS = ('error_code":154', "max distance limit")

MATRIX_FALLBACK_MAX_ROUTE_CALLS = 60
MATRIX_FALLBACK_BUDGET_S = 30.0
MATRIX_MAX_SOURCES = 5
MATRIX_MAX_TARGETS = 5

ALTERNATES_MAX = 2


VALHALLA_COSTINGS = frozenset(
    {
        "auto",
        "bicycle",
        "bus",
        "motorcycle",
        "motor_scooter",
        "pedestrian",
        "taxi",
        "truck",
    }
)


def costing_for(profile: str | None) -> str:
    """The engine's name for a client's transport profile."""
    name = constants.normalize_transport(profile)
    return name if name in VALHALLA_COSTINGS else "pedestrian"


STEP_PENALTY = 3600.0


def costing_options_for(profile: str | None, step_free: bool) -> dict[str, Any]:
    """The costing tune a step-free walk is measured with — nothing for any other."""
    name = costing_for(profile)
    if not step_free or name != "pedestrian":
        return {}
    return {name: {"step_penalty": STEP_PENALTY}}

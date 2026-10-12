"""Numeric limits and marker tuples for the Valhalla client."""

from __future__ import annotations

PRECISION = 1e6

LOCATION_SNAP_RADIUS_M = 100

ROUTE_SNAP_RADII_M = (LOCATION_SNAP_RADIUS_M, 500, 2000, 5000)
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


# What the engine answers to. A client's profile is not a costing: the wire says
# "car", Valhalla says `auto`, and a name it does not know is a 400 rather than a
# fallback — which is how a car request ended up with every leg unmeasured.
#
# The list is what this tile build can actually route, and nothing more.
# `multimodal` is deliberately absent: it needs transit (GTFS) tiles, and the
# stack is built from an OSM extract alone (docker-compose.yml), so it can only
# answer 400 — "locations are in unconnected regions". The remaining costings
# were each confirmed live against the running engine.
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
_PROFILE_ALIASES = {"car": "auto", "drive": "auto", "walk": "pedestrian"}


def costing_for(profile: str | None) -> str:
    """The engine's name for a client's transport profile.

    An unknown or unsupported profile becomes a walk: the one costing this
    dataset is certainly routable with. Every caller that hands a name to the
    engine goes through here, so a raw alias can never reach it.
    """
    name = (profile or "pedestrian").strip().lower()
    name = _PROFILE_ALIASES.get(name, name)
    return name if name in VALHALLA_COSTINGS else "pedestrian"

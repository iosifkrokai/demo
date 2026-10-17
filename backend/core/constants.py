"""Domain constants — models, weights, thresholds, limits."""

from __future__ import annotations

EARTH_RADIUS_M = 6_371_000.0
EARTH_RADIUS_KM = 6_371.0

TRANSPORT_ALIASES: dict[str, str] = {
    "pedestrian": "pedestrian",
    "walk": "pedestrian",
    "foot": "pedestrian",
    "bicycle": "bicycle",
    "bike": "bicycle",
    "auto": "auto",
    "car": "auto",
    "drive": "auto",
    "truck": "truck",
    "bus": "bus",
    "motorcycle": "motorcycle",
    "motor_scooter": "motor_scooter",
    "any": "any",
}


def normalize_transport(profile: str | None) -> str:
    """The canonical name of a client's transport profile."""
    name = (profile or "pedestrian").strip().lower()
    return TRANSPORT_ALIASES.get(name, name)


MIN_LEG_POINTS = 2
MIN_TOUR_STOPS = 3

MAX_TOOL_CALLS = 12
MAX_PLACES_PER_FIND = 8
MAX_CATALOGUE_PLACES = 60

REFINE_DEADLINE_S = 40.0
REPAIR_MIN_LEFT_S = 45.0
VALHALLA_ORDER_MIN_LEFT_S = 18.0
VALHALLA_ORDER_TIMEOUT_S = 10.0
VALHALLA_ORDER_RETRIES = 0


RRF_K = 60

VALHALLA_MAX_LOCATIONS = 20

RETRIEVAL_POOL_SIZE = 50


WALK_TOO_FAR_KM = 15.0
WALK_TOO_LONG_MINUTES = 240

UNREACHABLE_S = 10**9

UNKNOWN_S = float("nan")


MIN_BUDGET_MIN = 15
MAX_BUDGET_MIN = 480

VALHALLA_TIMEOUT_S = 20.0
VALHALLA_MAX_RETRIES = 2

MAX_WALK_LEG_KM = 5.0
DUPLICATE_RADIUS_M = 150.0
DUPLICATE_NAME_RADIUS_M = 500.0

PEDESTRIAN_SPEED_KMH = 4.0
BICYCLE_SPEED_KMH = 15.0
AUTO_SPEED_KMH = 45.0
BICYCLE_LEG_KM = 20.0
DRIVE_LEG_FALLBACK_S = 4 * 3600

WALK_LEG_BUDGET_SHARE = 0.25

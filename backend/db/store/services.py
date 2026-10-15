"""What may be highlighted beside a route, and how far off the line it may be."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from core import constants

MAX_OFF_LINE_M: dict[str, float] = {
    "pedestrian": 150.0,
    "bicycle": 250.0,
    "car": 400.0,
    "any": 250.0,
}
DEFAULT_PROFILE = "pedestrian"

MAX_SERVICES = 12
MAX_LINE_POINTS = 2000

STEP_CATEGORY = "лестница"

MAX_STEPS_ON_ROUTE = 50

STEP_ON_LINE_M = 1.0

_PROFILE_ALIASES: dict[str, str] = {
    "pedestrian": "pedestrian",
    "bicycle": "bicycle",
    "auto": "car",
    "car": "car",
    "truck": "car",
    "bus": "car",
    "motorcycle": "car",
    "motor_scooter": "car",
    "any": "any",
}


def threshold_for(profile: str, max_off_line_m: float | None = None) -> float:
    """How far off the line this profile is willing to go, in metres."""
    if max_off_line_m is not None:
        return float(max_off_line_m)
    key = _PROFILE_ALIASES.get(profile, DEFAULT_PROFILE)
    return MAX_OFF_LINE_M[key]


def service_codes(categories: Iterable[str] | None, known: Sequence[str]) -> list[str]:
    """The categories a service request means, given the taxonomy's default list.

    ``known`` is the highlighted service list the repository hands in, so no categories
    named means exactly those — a stairway is a service the taxonomy does not highlight
    and this default does not show.
    """
    allowed = set(known)
    if categories is None:
        return list(known)
    return [code for code in categories if code in allowed]


def named_codes(categories: Iterable[str] | None) -> list[str]:
    """The category codes a caller named, kept as given — role is not consulted."""
    return [str(code) for code in categories or () if code]


def route_line(shape: Any) -> dict[str, Any]:
    """Validate a GeoJSON LineString and return it unchanged."""
    if not isinstance(shape, dict) or shape.get("type") != "LineString":
        raise ValueError("shape_not_linestring")
    coords = shape.get("coordinates")
    if not isinstance(coords, list) or len(coords) < constants.MIN_LEG_POINTS:
        raise ValueError("shape_needs_two_points")
    if len(coords) > MAX_LINE_POINTS:
        raise ValueError("shape_too_long")
    for point in coords:
        if (
            not isinstance(point, (list, tuple))
            or len(point) < constants.MIN_LEG_POINTS
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in point[:2])
        ):
            raise ValueError("shape_bad_point")
    return shape


def item_of(row: dict[str, Any], line_m: float) -> dict[str, Any]:
    """One service, with its measurement named for what it actually is."""
    hours = (row.get("opening_hours") or "").strip()
    fraction = float(row.get("along_fraction") or 0.0)
    return {
        "id": row["id"],
        "source_url": row["source_url"],
        "name": row["name"],
        "category": row["category"],
        "town": row.get("town"),
        "lat": row["lat"],
        "lon": row["lon"],
        "opening_hours": hours or None,
        "hours_known": bool(hours),
        "off_line_m": round(float(row.get("off_line_m") or 0.0)),
        "along_m": round(max(0.0, min(1.0, fraction)) * line_m),
        "along_fraction": round(fraction, 4),
        "detour_confirmed": False,
    }


def empty_answer(
    codes: Sequence[str], profile: str, max_off_line_m: float | None, *, reason: str
) -> dict[str, Any]:
    """An empty answer that says why it is empty."""
    return {
        "items": [],
        "measured": "distance_to_line",
        "not_measured": "detour_walking_time",
        "detour_confirmed": False,
        "profile": profile,
        "categories": list(codes),
        "max_off_line_m": max_off_line_m,
        "line_m": 0,
        "result_cap": MAX_SERVICES,
        "capped": False,
        "reason": reason,
    }

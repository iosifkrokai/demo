"""What counts as a service beside a route, and how far off the line it may be.

Policy only: no SQL, no connection. The one query that uses these lives on
`PostgresPlaceRepository.services_along`, which is where queries belong now.

A service is never a stop, and geometry is measured, never guessed as a detour.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

MAX_OFF_LINE_M: dict[str, float] = {
    "pedestrian": 150.0,
    "bicycle": 250.0,
    "car": 400.0,
    "any": 250.0,
}
DEFAULT_PROFILE = "pedestrian"

MAX_SERVICES = 12
MAX_LINE_POINTS = 2000

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


def service_codes(categories: Iterable[str] | None = None) -> list[str]:
    """The categories that count as services, from the taxonomy alone.

    Unknown or non-service codes are dropped rather than trusted.
    """
    from reference.taxonomy import all_categories

    known = [cat.code for cat in all_categories() if getattr(cat, "role", None) == "service"]
    if categories is None:
        return known
    return [code for code in categories if code in known]


def route_line(shape: Any) -> dict:
    """Validate a GeoJSON LineString and return it unchanged.

    Raises `ValueError` with a machine-readable reason, never an empty answer.
    """
    if not isinstance(shape, dict) or shape.get("type") != "LineString":
        raise ValueError("shape_not_linestring")
    coords = shape.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        raise ValueError("shape_needs_two_points")
    if len(coords) > MAX_LINE_POINTS:
        raise ValueError("shape_too_long")
    for point in coords:
        if (
            not isinstance(point, (list, tuple))
            or len(point) < 2
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

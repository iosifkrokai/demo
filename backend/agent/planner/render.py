"""Step 8 — Render the canonical route via Valhalla /route.

On a whole-tour refusal it falls back to drawing leg by leg.
"""

from __future__ import annotations

import logging
from itertools import pairwise

from contracts.planner import Candidate, LatLon
from core.errors import UpstreamUnavailable
from domain import constants
from infra.valhalla_client import RouteStatus, route_through

log = logging.getLogger(__name__)


def render(
    route: list[Candidate],
    costing: str = "pedestrian",
    origin: LatLon | None = None,
    locale: str = "ru",
    round_trip: bool = False,
) -> tuple[dict, dict, str]:
    """Call Valhalla /route. Returns (shape_geojson, summary_dict, status_code).

    `origin` is the fixed start; `round_trip` closes the tour on its own start.
    """
    if len(route) < 2:
        return {}, {}, "no_route_exists"

    pts = [(p.lat, p.lon) for p in route]
    if origin is not None:
        pts = [(origin.lat, origin.lon), *pts]
    if round_trip:
        pts = [*pts, pts[0]]

    locations = [
        {
            "lat": lat,
            "lon": lon,
            "type": "break" if i in (0, len(pts) - 1) else "via",
        }
        for i, (lat, lon) in enumerate(pts)
    ]

    if len(locations) > constants.VALHALLA_MAX_LOCATIONS:
        log.info(
            "route: %d locations exceed Valhalla's %d cap — rendering leg by leg",
            len(locations), constants.VALHALLA_MAX_LOCATIONS,
        )
        shape, summary, leg_status = _render_legs(pts, costing, locale)
        if leg_status == "usable" and shape.get("coordinates"):
            return shape, summary, "usable"
        return {}, {}, "empty_geometry"

    try:
        result = route_through(locations, costing=costing, language=locale)
    except UpstreamUnavailable as exc:
        log.warning("route: /route failed for the whole tour (%s) — trying legs", exc)
        result = None

    if result and result.status == RouteStatus.USABLE:
        if result.language != locale:
            log.warning("route: locale mismatch (requested %s, got %s)", locale, result.language)
            return result.shape, result.summary or {}, "locale_mismatch"

        if result.maneuvers:
            missing = _verify_maneuver_fields(result.maneuvers)
            if missing:
                log.warning("route: missing maneuver fields: %s", missing)
                return result.shape, result.summary or {}, "missing_maneuver_data"

        return result.shape, result.summary or {}, "usable"

    if result and result.status == RouteStatus.SERVICE_UNAVAILABLE:
        return {}, {}, "service_unavailable"

    if result and result.status == RouteStatus.NO_ROUTE_EXISTS and len(pts) < 3:
        return {}, {}, "no_route_exists"

    shape, summary, leg_status = _render_legs(pts, costing, locale)
    if leg_status == "usable" and shape.get("coordinates"):
        return shape, summary, "usable"

    log.warning("route: no leg of the tour could be drawn — empty shape")
    return {}, {}, "empty_geometry"


def _verify_maneuver_fields(maneuvers: list[dict]) -> list[str]:
    """Verify that required maneuver fields are present.

    Returns the missing field names; a maneuver without an instruction is blank.
    """
    missing_fields: list[str] = []
    required_fields = ["instruction", "length", "time", "type"]

    for i, maneuver in enumerate(maneuvers):
        for field in required_fields:
            if field not in maneuver or maneuver[field] is None:
                missing_fields.append(f"maneuver_{i}_{field}")

    return missing_fields


def _render_legs(pts: list[tuple[float, float]], costing: str, locale: str) -> tuple[dict, dict, str]:
    """Draw every consecutive pair on its own and keep the legs that route.

    A leg Valhalla refuses is skipped, so one bad stop costs its two legs only.
    """
    coords: list[list[float]] = []
    length_km = 0.0
    seconds = 0.0
    drawn = 0

    for (a_lat, a_lon), (b_lat, b_lon) in pairwise(pts):
        pair = [
            {"lat": a_lat, "lon": a_lon, "type": "break"},
            {"lat": b_lat, "lon": b_lon, "type": "break"},
        ]
        try:
            result = route_through(pair, costing=costing, language=locale)
        except UpstreamUnavailable:
            continue

        if result.status != RouteStatus.USABLE:
            continue

        if not result.shape.get("coordinates"):
            continue

        coords.extend(result.shape["coordinates"])
        drawn += 1
        length_km += float((result.summary or {}).get("length") or 0.0)
        seconds += float((result.summary or {}).get("time") or 0.0)

    if not drawn:
        return {}, {}, "no_route_exists"

    log.info("route: drew %d of %d legs separately", drawn, len(pts) - 1)
    return {"type": "LineString", "coordinates": coords}, {
        "length": length_km,
        "time": seconds,
    }, "usable"

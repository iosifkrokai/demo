"""Step 8 — Render the canonical route via Valhalla /route.

Takes the ordered Candidate list, calls Valhalla, returns (shape, summary, status).
The first and last waypoints are 'break' (start/end of walking tour);
intermediate waypoints are 'via' (must pass through).

Valhalla is the only thing that draws geometry here. When it refuses the tour
as a whole — a stop on a road island that is not connected to the rest of the
network ("No path could be found for input"), a via it cannot pass in order —
the tour is requested leg by leg and the legs that do route are kept, so the
map gets a line instead of points with nothing between them.

Returns honest status codes (machine-readable) instead of empty/degenerate routes.
"""

from __future__ import annotations

import logging
from itertools import pairwise

from ..errors import UpstreamUnavailable
from ..models import Candidate, LatLon
from ..valhalla_client import route_through, RouteStatus

log = logging.getLogger(__name__)


def render(
    route: list[Candidate],
    costing: str = "pedestrian",
    origin: LatLon | None = None,
    locale: str = "ru",
) -> tuple[dict, dict, str]:
    """Call Valhalla /route. Returns (shape_geojson, summary_dict, status_code).

    `origin` (tourist's GPS position) becomes the fixed start of the shape.
    `locale` is the requested language for instructions (e.g., "ru" or "en").
    
    Returns honest status codes:
    - "usable": valid route with geometry
    - "no_route_exists": no route can be built between these points
    - "service_unavailable": Valhalla service unavailable/timeout
    - "empty_geometry": route returned but has no geometry
    - "locale_mismatch": requested locale does not match response
    - "missing_maneuver_data": required maneuver fields are missing
    """
    if len(route) < 2:
        return {}, {}, "no_route_exists"

    pts = [(p.lat, p.lon) for p in route]
    if origin is not None:
        pts = [(origin.lat, origin.lon), *pts]

    locations = [
        {
            "lat": lat,
            "lon": lon,
            "type": "break" if i in (0, len(pts) - 1) else "via",
        }
        for i, (lat, lon) in enumerate(pts)
    ]

    try:
        result = route_through(locations, costing=costing, language=locale)
    except UpstreamUnavailable as exc:
        log.warning("route: /route failed for the whole tour (%s) — trying legs", exc)
        result = None

    if result and result.status == RouteStatus.USABLE:
        # Verify locale matches
        if result.language != locale:
            log.warning("route: locale mismatch (requested %s, got %s)", locale, result.language)
            return result.shape, result.summary or {}, "locale_mismatch"
        
        # Verify maneuver data
        if result.maneuvers:
            missing = _verify_maneuver_fields(result.maneuvers)
            if missing:
                log.warning("route: missing maneuver fields: %s", missing)
                return result.shape, result.summary or {}, "missing_maneuver_data"
        
        return result.shape, result.summary or {}, "usable"

    if result and result.status == RouteStatus.SERVICE_UNAVAILABLE:
        return {}, {}, "service_unavailable"

    # A refused whole-tour request (400/442 — e.g. a stop on a disconnected road
    # island, see tests/test_road_island.py) is not the end of the road: the tour
    # is retried leg by leg. With only two points there is no leg to fall back to,
    # so the honest status stands.
    if result and result.status == RouteStatus.NO_ROUTE_EXISTS and len(pts) < 3:
        return {}, {}, "no_route_exists"

    # Try leg-by-leg fallback
    shape, summary, leg_status = _render_legs(pts, costing, locale)
    if leg_status == "usable" and shape.get("coordinates"):
        return shape, summary, "usable"
    
    log.warning("route: no leg of the tour could be drawn — empty shape")
    return {}, {}, "empty_geometry"


def _verify_maneuver_fields(maneuvers: list[dict]) -> list[str]:
    """Verify that required maneuver fields are present.
    
    Returns list of missing field names for maneuvers that lack them.
    A maneuver without an instruction is a problem — the guide would show blank text.
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

    A leg Valhalla refuses (an unreachable pair) is skipped, so one bad stop
    costs its two legs, not the whole line.
    
    Returns (shape, summary, status_code).
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

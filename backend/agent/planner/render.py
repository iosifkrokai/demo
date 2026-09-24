"""Step 8 — Render the canonical route via Valhalla /route.

Takes the ordered Candidate list, calls Valhalla, returns (shape, summary).
The first and last waypoints are 'break' (start/end of walking tour);
intermediate waypoints are 'via' (must pass through).

Valhalla is the only thing that draws geometry here. When it refuses the tour
as a whole — a stop on a road island that is not connected to the rest of the
network ("No path could be found for input"), a via it cannot pass in order —
the tour is requested leg by leg and the legs that do route are kept, so the
map gets a line instead of points with nothing between them.
"""

from __future__ import annotations

import logging
from itertools import pairwise

from ..errors import UpstreamUnavailable
from ..models import Candidate, LatLon
from ..valhalla_client import route_through

log = logging.getLogger(__name__)


def render(
    route: list[Candidate],
    costing: str = "pedestrian",
    origin: LatLon | None = None,
) -> tuple[dict, dict]:
    """Call Valhalla /route. Returns (shape_geojson, summary_dict).

    `origin` (tourist's GPS position) becomes the fixed start of the shape.
    """
    if len(route) < 2:
        return {}, {}

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
        shape, summary = route_through(locations, costing=costing)
    except UpstreamUnavailable as exc:
        log.warning("route: /route failed for the whole tour (%s) — trying legs", exc)
        shape, summary = {}, {}

    if (shape or {}).get("coordinates"):
        return shape, summary or {}

    shape, summary = _render_legs(pts, costing)
    if not (shape or {}).get("coordinates"):
        log.warning("route: no leg of the tour could be drawn — empty shape")
    return shape, summary


def _render_legs(pts: list[tuple[float, float]], costing: str) -> tuple[dict, dict]:
    """Draw every consecutive pair on its own and keep the legs that route.

    A leg Valhalla refuses (an unreachable pair) is skipped, so one bad stop
    costs its two legs, not the whole line.
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
            leg_shape, leg_summary = route_through(pair, costing=costing)
        except UpstreamUnavailable:
            continue
        if not (leg_shape or {}).get("coordinates"):
            continue
        coords.extend(leg_shape["coordinates"])
        drawn += 1
        length_km += float((leg_summary or {}).get("length") or 0.0)
        seconds += float((leg_summary or {}).get("time") or 0.0)

    if not drawn:
        return {}, {}
    log.info("route: drew %d of %d legs separately", drawn, len(pts) - 1)
    return {"type": "LineString", "coordinates": coords}, {
        "length": length_km,
        "time": seconds,
    }

"""Step 8 — Render the canonical route via Valhalla /route.

Takes the ordered Candidate list, calls Valhalla, returns (shape, summary).
The first and last waypoints are 'break' (start/end of walking tour);
intermediate waypoints are 'via' (must pass through).

If Valhalla fails (network, malformed coords), returns ({}, {}). The caller
decides whether to treat this as a hard error or to fall back to a
straight-line shape from the ordered coords.
"""

from __future__ import annotations

from ..models import Candidate, LatLon
from ..valhalla_client import route_through


def render(
    route: list[Candidate],
    costing: str = "pedestrian",
    origin: LatLon | None = None,
) -> tuple[dict, dict]:
    """Call Valhalla /route. Returns (shape_geojson, summary_dict).

    Network failures are handled inside valhalla_client (retries → 503),
    so this function does not raise; it returns ({}, {}) if the response
    is malformed. Callers fall back to a straight-line shape if needed.
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
    shape, summary = route_through(locations, costing=costing)
    return shape, summary or {}

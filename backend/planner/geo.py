"""Geographic focus: keep a walking route local around its anchor.

Anchor priority: tourist GPS > must-visit/named place > densest cluster.
"""

from __future__ import annotations

from core import constants
from planner.models import LatLon

_TRACE_NAMES_MAX = 15


def should_skip_geo_focus(*, region_scope: bool, origin: LatLon | None) -> bool:
    """May the region-scope rule keep its spread, or must the focus still run?

    Region scope skips only the anchor-town focus; GPS is always honoured.
    """
    return region_scope and origin is None


def _geo_focus(
    candidates: list,
    origin: LatLon | None = None,
    anchor_id: int | None = None,
) -> list:
    """Drop candidates too far from the anchor.

    Anchor priority: tourist GPS > must-visit/named place > densest cluster.
    """
    keep, _ = _geo_focus_report(candidates, origin=origin, anchor_id=anchor_id)
    return keep


def _geo_focus_report(
    candidates: list,
    origin: LatLon | None = None,
    anchor_id: int | None = None,
) -> tuple[list, dict]:
    """``_geo_focus`` plus the reasoning, for the trace.

    The radius is reported as finally used (the discovery branch widens it).
    """
    if not candidates:
        return [], {"anchor": None, "radius_km": None, "dropped": []}
    if origin is not None:
        anchor = origin
        anchor_name = "GPS"
        def dist(c):
            return _distance_from_origin_m(c, origin)
    elif anchor_id is not None and any(c.id == anchor_id for c in candidates):
        anchor = next(c for c in candidates if c.id == anchor_id)
        anchor_name = anchor.name
        def dist(c):
            return _distance_m(c, anchor)
    else:
        focus_m = constants.GEO_FOCUS_KM * 1000
        anchor = max(
            candidates,
            key=lambda c: (
                sum(1 for o in candidates if _distance_m(o, c) <= focus_m),
                c.rrf_score,
            ),
        )
        anchor_name = anchor.name
        def dist(c):
            return _distance_m(c, anchor)
    max_m = constants.GEO_FOCUS_KM * 1000

    if origin is not None or anchor_id is not None:
        keep = [c for c in candidates if c is anchor or dist(c) <= max_m]
        return keep, _geo_report(anchor_name, max_m, candidates, keep, dist)

    discovery_max_m = constants.GEO_FOCUS_DISCOVERY_MAX_KM * 1000
    while True:
        keep = [
            c for c in candidates
            if c is anchor or dist(c) <= max_m
        ]
        if len(keep) >= 3 or max_m >= discovery_max_m:
            return keep, _geo_report(anchor_name, max_m, candidates, keep, dist)
        max_m = min(max_m * 2, discovery_max_m)


def _geo_report(
    anchor_name: str,
    max_m: float,
    candidates: list,
    keep: list,
    dist,
) -> dict:
    """Which anchor was chosen, how wide the radius ended up, who fell outside.

    The radius is reported as finally used, not as the constant it started from.
    """
    kept_ids = {id(c) for c in keep}
    dropped = [c for c in candidates if id(c) not in kept_ids]
    return {
        "anchor": anchor_name,
        "radius_km": round(max_m / 1000, 1),
        "dropped": [
            {"name": c.name, "km": round(dist(c) / 1000, 1)}
            for c in dropped[:_TRACE_NAMES_MAX]
        ],
    }


def _distance_m(a, b) -> float:
    """Equirectangular distance in metres (fine at city scale)."""
    return _distance_pt_m(a.lat, a.lon, b.lat, b.lon)


def _distance_pt_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular distance in metres (fine at city scale)."""
    import math

    lat_mid = math.radians((lat1 + lat2) / 2)
    dx = math.radians(lon1 - lon2) * 6_371_000 * math.cos(lat_mid)
    dy = math.radians(lat1 - lat2) * 6_371_000
    return math.hypot(dx, dy)


def _distance_from_origin_m(c, origin: LatLon) -> float:
    return _distance_pt_m(c.lat, c.lon, origin.lat, origin.lon)

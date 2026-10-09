"""Geographic focus: keep a walking route local around its anchor.

Anchor priority: tourist GPS > must-visit/named place > densest cluster.
Keyword-mode retrieval (no embeddings) can surface matching places from across
the whole voblast; a walking route must stay local.
"""

from __future__ import annotations

from contracts.planner import LatLon
from domain import constants

#: How many names a step's "what went" list carries into the trace. The count is
#: always exact; only the examples are capped, because a span is read by a person
#: and two hundred names are a wall rather than an answer.
_TRACE_NAMES_MAX = 15


def should_skip_geo_focus(*, region_scope: bool, origin: LatLon | None) -> bool:
    """May the region-scope rule keep its spread, or must the focus still run?

    A region-wide request («все костёлы Гродненской области») legitimately keeps its
    spread: confining it to one walkable cluster is what made that question
    unanswerable. The tourist's own position is not a cluster preference, though —
    it is the start of the walk. Measured before this rule: the same query with an
    `origin` AND a 120-minute budget came back as two stops 177 km apart with 38
    hours of walking, because the focus was skipped wholesale, GPS included. So GPS
    is always honoured, and region scope is allowed to skip only the anchor-town
    focus. A catalogue request never reaches here (it returns before this step).
    """
    return region_scope and origin is None


def _geo_focus(
    candidates: list,
    origin: LatLon | None = None,
    anchor_id: int | None = None,
) -> list:
    """Drop candidates too far from the anchor.

    Anchor priority: tourist GPS > must-visit/named place > densest cluster.
    Keyword-mode retrieval (no embeddings) can surface matching places from
    across the whole voblast; a walking route must stay local.
    """
    keep, _ = _geo_focus_report(candidates, origin=origin, anchor_id=anchor_id)
    return keep


def _geo_focus_report(
    candidates: list,
    origin: LatLon | None = None,
    anchor_id: int | None = None,
) -> tuple[list, dict]:
    """``_geo_focus`` plus the reasoning, for the trace.

    «8 остановок убрано» does not answer the question a reader actually has —
    «почему этот костёл не в маршруте». The radius and each dropped place's own
    distance do: a stop 12 km away under a 4 km radius is a different story from
    one 4.2 km away under a 4 km radius, and only the numbers tell them apart.
    """
    if not candidates:
        return [], {"anchor": None, "radius_km": None, "dropped": []}
    if origin is not None:
        anchor = origin
        anchor_name = "GPS"
        dist = lambda c: _distance_from_origin_m(c, origin)
    elif anchor_id is not None and any(c.id == anchor_id for c in candidates):
        anchor = next(c for c in candidates if c.id == anchor_id)
        anchor_name = anchor.name
        dist = lambda c: _distance_m(c, anchor)
    else:
        # Vague discovery query with no anchor: taking the single top-ranked hit
        # as the anchor can land on an outlier — "достопримечательности
        # Гродненской области" anchored on a fortress ring spread over 20 km,
        # where no walking leg is possible and the optimizer answered 422.
        # Pick the densest cluster instead: the candidate with the most
        # neighbours inside one GEO_FOCUS_KM radius.
        focus_m = constants.GEO_FOCUS_KM * 1000
        anchor = max(
            candidates,
            key=lambda c: (
                sum(1 for o in candidates if _distance_m(o, c) <= focus_m),
                c.rrf_score,
            ),
        )
        anchor_name = anchor.name
        dist = lambda c: _distance_m(c, anchor)
    max_m = constants.GEO_FOCUS_KM * 1000

    if origin is not None or anchor_id is not None:
        # A known position (tourist GPS) or a named place anchors the walk:
        # never inflate the radius here.  Doubling used to pull 50 km-away
        # castles into "Мирский замок", and the optimizer then dropped
        # everything but one stop because no leg was walkable.
        keep = [c for c in candidates if c is anchor or dist(c) <= max_m]
        return keep, _geo_report(anchor_name, max_m, candidates, keep, dist)

    # No anchor at all (vague discovery query): stay local around the best
    # candidate. Widening the radius here used to pull stops hundreds of km
    # apart into one walking tour, and the optimizer then gave up with
    # 422 "optimizer could not produce a route with ≥ 2 stops".
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

    The radius is reported as it was finally used, not as the constant it
    started from: the discovery branch doubles it until it has three stops, and
    a reader comparing «убрано 40» against a 4 km constant would conclude the
    code is broken rather than that the walk was widened.
    """
    kept_ids = {id(c) for c in keep}
    dropped = [c for c in candidates if id(c) not in kept_ids]
    return {
        "anchor": anchor_name,
        "radius_km": round(max_m / 1000, 1),
        # Distance to the anchor, so «убрано 40» becomes «этот — в 12 км, тот — в 4.2».
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

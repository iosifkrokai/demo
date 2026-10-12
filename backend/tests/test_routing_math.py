"""Pure math of the routing leaf: no database, no network, no model.

The first target is ``routing/valhalla/types.py`` — the time sentinels and the
result shape every route call speaks. The rest is the small amount of the leaf
that is pure at import and worth pinning.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from core import constants
from core.errors import UpstreamUnavailableError
from routing.models import (
    Candidate,
    CostMatrix,
    LatLon,
    OriginAnchor,
    ResolvedConstraints,
)
from routing.optimize import optimize
from routing.valhalla import constants as valhalla
from routing.valhalla.matrix import is_route_failure
from routing.valhalla.types import RouteResult, RouteStatus, is_unknown_time, is_unreachable_time
from routing.validate import validate


def test_route_status_values_are_the_codes_the_wire_speaks() -> None:
    assert RouteStatus.USABLE.value == "usable"
    assert RouteStatus.NO_ROUTE_EXISTS.value == "no_route_exists"
    assert RouteStatus.SERVICE_UNAVAILABLE.value == "service_unavailable"
    assert RouteStatus.EMPTY_GEOMETRY.value == "empty_geometry"


def test_unreachable_time_is_the_alone_sentinel() -> None:
    """Only UNREACHABLE_S costs a stop; a real time never reads as it."""
    assert is_unreachable_time(float(constants.UNREACHABLE_S)) is True
    assert is_unreachable_time(0.0) is False
    assert is_unreachable_time(125.0) is False


def test_unknown_time_is_the_nan_we_could_not_ask_for() -> None:
    """UNKNOWN_S means "not measured" and must not be confused with a real 0.0."""
    assert math.isnan(constants.UNKNOWN_S)
    assert is_unknown_time(constants.UNKNOWN_S) is True
    assert is_unknown_time(0.0) is False
    assert is_unreachable_time(constants.UNKNOWN_S) is False


def test_a_route_result_carries_its_status_and_geometry() -> None:
    shape = {"type": "LineString", "coordinates": [[23.83, 53.68]]}
    result = RouteResult(status=RouteStatus.USABLE, shape=shape, summary={"time": 12.0})
    assert result.status is RouteStatus.USABLE
    assert result.shape == shape
    assert result.summary == {"time": 12.0}
    assert result.maneuvers is None
    assert result.language is None


def test_route_failure_recognises_the_tour_not_the_connection() -> None:
    """A snap or no-path message is the tour's fault; transport failures bubble up."""
    assert is_route_failure(UpstreamUnavailableError("no path could be found between 2 points"))
    assert is_route_failure(UpstreamUnavailableError("candidate edge not found"))
    assert not is_route_failure(UpstreamUnavailableError("connection refused"))


def test_snap_radii_start_broad_and_widen() -> None:
    assert valhalla.ROUTE_SNAP_RADII_M[0] == valhalla.LOCATION_SNAP_RADIUS_M
    assert list(valhalla.ROUTE_SNAP_RADII_M) == sorted(valhalla.ROUTE_SNAP_RADII_M)


def test_precision_is_the_polyline_scale() -> None:
    assert valhalla.PRECISION == 1e6


def test_latlon_accepts_the_region_and_refuses_the_globe() -> None:
    assert LatLon(lat=53.68, lon=23.83).lat == 53.68
    with pytest.raises(ValidationError):
        LatLon(lat=0.0, lon=0.0)


# --- the tourist's own position as a leg endpoint -----------------------------
#
# The origin is not a stop, so it is not a matrix row: it travels as the anchor
# beside the matrix. What it must change is the arithmetic — the walk out of and
# back to where the tourist stands is part of the tour, and a plan that ignores it
# reports a budget for a walk nobody was ever offered.


def _candidate(pid: int, lat: float, lon: float) -> Candidate:
    return Candidate(id=pid, name=f"P{pid}", category="church", lat=lat, lon=lon)


def _cost(walk: list[list[float]], anchor: OriginAnchor | None = None) -> CostMatrix:
    n = len(walk)
    return CostMatrix(
        walk_seconds=walk, visit_minutes=[10] * n, indices=list(range(n)), anchor=anchor
    )


def test_the_budget_counts_the_walk_out_of_the_tourists_position() -> None:
    """The same stops are a different answer depending on where the tourist is."""
    stops = [_candidate(1, 53.6771, 23.8258), _candidate(2, 53.6819, 23.8400)]
    plain = _cost([[0.0, 300.0], [300.0, 0.0]])
    counted = _cost(
        [[0.0, 300.0], [300.0, 0.0]],
        OriginAnchor(from_origin=[600.0, 60.0], to_origin=[900.0, 90.0]),
    )
    constraints = ResolvedConstraints(time_budget_minutes=30)  # 1800 s

    without = validate(stops, plain, constraints, {"order": [0, 1]})
    with_origin = validate(stops, counted, constraints, {"order": [0, 1]})

    # 300 s of walking, plus 20 min of visits, fits 30 min. Add the 600 s walk out
    # of the tourist's position and it does not.
    assert without.fits_budget is True
    assert with_origin.fits_budget is False
    assert with_origin.walk_seconds - without.walk_seconds == pytest.approx(600.0)


def test_a_round_trip_closes_on_the_origin_when_it_is_known() -> None:
    """`render` draws the ring back to where the tourist stands; the budget must
    count the same ring, not a different one back to the first stop."""
    stops = [_candidate(1, 53.6771, 23.8258), _candidate(2, 53.6819, 23.8400)]
    chain = [[0.0, 300.0], [300.0, 0.0]]
    constraints = ResolvedConstraints(time_budget_minutes=120, round_trip=True)

    without = validate(stops, _cost(chain), constraints, {"order": [0, 1]})
    with_origin = validate(
        stops,
        _cost(chain, OriginAnchor(from_origin=[600.0, 60.0], to_origin=[900.0, 90.0])),
        constraints,
        {"order": [0, 1]},
    )

    assert without.walk_seconds == pytest.approx(600.0)  # 0→1 and 1→0
    # 600 s out of the origin + 300 between the stops + 90 back from the LAST stop.
    # Closing on the first stop instead — the pre-anchor behaviour — would add
    # matrix[1][0] = 300 and make this 1200.
    assert with_origin.walk_seconds == pytest.approx(990.0)
    assert with_origin.trace["max_leg_seconds"] == 600


def test_the_optimizer_starts_next_to_the_tourist() -> None:
    """A matrix that is cheap between the stops still has to be entered from where
    the tourist is standing — otherwise the tour opens across the city."""
    stops = [_candidate(1, 53.6771, 23.8258), _candidate(2, 53.6819, 23.8400)]
    stops.append(_candidate(3, 53.6900, 23.8500))
    walk = [[0.0, 1000.0, 1100.0], [1000.0, 0.0, 100.0], [1100.0, 100.0, 0.0]]
    # Stop 3 is a step from the tourist's position; the other two are kilometres away.
    anchor = OriginAnchor(from_origin=[5000.0, 4000.0, 1.0], to_origin=[5000.0, 4000.0, 1.0])
    constraints = ResolvedConstraints(time_budget_minutes=None)

    _, blind_info = optimize(stops, _cost(walk), constraints)
    aware, aware_info = optimize(stops, _cost(walk, anchor), constraints)

    assert aware_info["order"][0] == 2
    assert aware[0].id == 3
    # ...and the anchor is what changed it: without it the tour opens elsewhere.
    assert blind_info["order"][0] != 2


def test_a_two_stop_tour_opens_at_the_nearer_end() -> None:
    """Two stops have no order to search, but they do have a direction: without
    this the walk out of the tourist's position could be the long way round."""
    stops = [_candidate(1, 53.6771, 23.8258), _candidate(2, 53.6819, 23.8400)]
    walk = [[0.0, 600.0], [600.0, 0.0]]
    # Stop 2 is a step away; stop 1 is an hour's walk.
    anchor = OriginAnchor(from_origin=[4000.0, 20.0], to_origin=[4000.0, 20.0])
    constraints = ResolvedConstraints(time_budget_minutes=None)

    blind, _ = optimize(stops, _cost(walk), constraints)
    aware, aware_info = optimize(stops, _cost(walk, anchor), constraints)

    assert blind[0].id == 1  # unchanged when the position is not known
    assert aware_info["order"] == [1, 0]
    assert aware[0].id == 2


def test_an_unknown_leg_is_not_an_unreachable_one_for_the_anchor() -> None:
    """`UNKNOWN_S` means "could not ask": it must not turn the origin's legs into a
    verdict that the route is impossible."""
    stops = [_candidate(1, 53.6771, 23.8258), _candidate(2, 53.6819, 23.8400)]
    cost = _cost(
        [[0.0, 300.0], [300.0, 0.0]],
        OriginAnchor(from_origin=[math.nan, math.nan], to_origin=[math.nan, math.nan]),
    )
    plan = validate(stops, cost, ResolvedConstraints(time_budget_minutes=30), {"order": [0, 1]})

    assert plan.fits_budget is True
    # One unknown leg, not two: an open tour has the way out of the origin and the
    # chain, and only the first of those was unanswerable.
    assert plan.trace["walk_times_unknown"] == 1
    assert plan.walk_seconds < constants.UNREACHABLE_S

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
from routing.models import LatLon
from routing.valhalla import constants as valhalla
from routing.valhalla.matrix import is_route_failure
from routing.valhalla.types import RouteResult, RouteStatus, is_unknown_time, is_unreachable_time


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

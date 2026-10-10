"""route_through resilience: widen the snap radius, then drop unsnappable stops."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.errors import UpstreamUnavailable
from planner import valhalla_client as vc

GRODNO = {"lat": 53.6791, "lon": 23.8216, "type": "break"}
NEW_CASTLE = {"lat": 53.6849, "lon": 23.8310, "type": "via"}
OLD_TOWN = {"lat": 53.6787, "lon": 23.8279, "type": "break"}

TRIP = {
    "trip": {
        "legs": [{"shape": "_p~iF~ps|U_ulLnnqC"}],
        "summary": {"length": 1.2, "time": 900.0},
    }
}

SNAP_500 = (
    "valhalla GET http://valhalla/route failed after retries: "
    'server error: {"error_code":499,"error":"Could not find candidate edge used for '
    'destination label"}'
)


def _radii_of(params: dict) -> list[int]:
    payload = json.loads(params["json"])
    return [loc["radius"] for loc in payload["locations"]]


@pytest.fixture(autouse=True)
def _clear_snap_cache():
    """The /locate memo is process-global; keep it from leaking between tests."""
    vc._SNAP_CACHE.clear()
    yield
    vc._SNAP_CACHE.clear()


def _locate_ok(url: str, lat: float = 53.6791, lon: float = 23.8216) -> list[dict] | None:
    """A canned /locate answer, or None when this call is not a locate probe."""
    if not url.endswith("/locate"):
        return None
    return [{"edges": [{"correlated_lat": lat, "correlated_lon": lon}]}]


def test_first_radius_is_used_when_it_works(monkeypatch):
    seen: list[dict] = []

    def fake(method, url, *, params, timeout):
        if (locate := _locate_ok(url)) is not None:
            return locate
        seen.append(params)
        return TRIP

    monkeypatch.setattr(vc, "_request_with_retry", fake)
    result = vc.route_through([GRODNO, NEW_CASTLE, OLD_TOWN])

    assert result.status == vc.RouteStatus.USABLE
    assert result.shape["type"] == "LineString"
    assert result.summary["length"] == 1.2
    assert len(seen) == 1
    assert _radii_of(seen[0]) == [vc.LOCATION_SNAP_RADIUS_M] * 3


def test_snap_failure_retries_with_a_wider_radius(monkeypatch):
    radii_tried: list[int] = []

    def fake(method, url, *, params, timeout):
        if (locate := _locate_ok(url)) is not None:
            return locate
        r = _radii_of(params)[0]
        radii_tried.append(r)
        if r == vc.LOCATION_SNAP_RADIUS_M:
            raise UpstreamUnavailable(SNAP_500)
        return TRIP

    monkeypatch.setattr(vc, "_request_with_retry", fake)
    result = vc.route_through([GRODNO, NEW_CASTLE, OLD_TOWN])

    assert result.status == vc.RouteStatus.USABLE
    assert result.shape["coordinates"], "expected a shape once snapping succeeded"
    assert radii_tried == [vc.ROUTE_SNAP_RADII_M[0], vc.ROUTE_SNAP_RADII_M[1]]


def test_non_snap_error_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def fake(method, url, *, params, timeout):
        if (locate := _locate_ok(url)) is not None:
            return locate
        calls["n"] += 1
        raise UpstreamUnavailable("valhalla GET /route failed after retries: connect timeout")

    monkeypatch.setattr(vc, "_request_with_retry", fake)
    result = vc.route_through([GRODNO, NEW_CASTLE, OLD_TOWN])
    assert result.status == vc.RouteStatus.SERVICE_UNAVAILABLE
    assert calls["n"] == 1, "a plain upstream error must not fan out into radius retries"


def test_unsnappable_stop_is_dropped_before_routing(monkeypatch):
    """A stop with no edge is dropped up front, endpoints included."""
    routed: list[list[dict]] = []

    def fake(method, url, *, params, timeout):
        payload = json.loads(params["json"])
        if "/locate" in url:
            if payload["locations"][0]["lat"] == NEW_CASTLE["lat"]:
                return [{"edges": []}]
            return [{"edges": [{"correlated_lat": 53.68, "correlated_lon": 23.82}]}]
        routed.append(payload["locations"])
        return TRIP

    monkeypatch.setattr(vc, "_request_with_retry", fake)
    result = vc.route_through([GRODNO, NEW_CASTLE, OLD_TOWN])

    assert result.status == vc.RouteStatus.USABLE
    assert result.shape["coordinates"], "expected the tour to survive without that stop"
    assert len(routed) == 1, "no radius ladder needed once the bad stop is gone"
    assert len(routed[0]) == 2, "the unsnappable middle stop should be gone"
    assert [loc["lat"] for loc in routed[0]] == [53.68, 53.68]


def test_unsnappable_endpoint_is_dropped_too(monkeypatch):
    """The first/last stop must not poison the whole tour."""

    def fake(method, url, *, params, timeout):
        payload = json.loads(params["json"])
        if "/locate" in url:
            if payload["locations"][0]["lat"] == GRODNO["lat"]:
                return [{"edges": []}]
            return [{"edges": [{"correlated_lat": 53.68, "correlated_lon": 23.82}]}]
        return TRIP

    monkeypatch.setattr(vc, "_request_with_retry", fake)
    result = vc.route_through([GRODNO, NEW_CASTLE, OLD_TOWN])

    assert result.status == vc.RouteStatus.USABLE
    assert result.shape["coordinates"], "a bad endpoint must not kill the route"


def test_single_routable_pair_still_builds(monkeypatch):
    """With only one snappable stop left there is no route — and no exception."""

    def fake(method, url, *, params, timeout):
        payload = json.loads(params["json"])
        if "/locate" in url:
            if payload["locations"][0]["lat"] == GRODNO["lat"]:
                return [{"edges": [{"correlated_lat": 53.6791, "correlated_lon": 23.8216}]}]
            return [{"edges": []}]
        return TRIP

    monkeypatch.setattr(vc, "_request_with_retry", fake)
    result = vc.route_through([GRODNO, NEW_CASTLE, OLD_TOWN])
    assert result.status == vc.RouteStatus.NO_ROUTE_EXISTS
    assert result.shape == {}
    assert result.summary is None


def test_fewer_than_two_locations_is_a_noop(monkeypatch):
    def fake(method, url, *, params, timeout):
        raise AssertionError("must not call Valhalla with a single location")

    monkeypatch.setattr(vc, "_request_with_retry", fake)
    result = vc.route_through([GRODNO])
    assert result.status == vc.RouteStatus.NO_ROUTE_EXISTS
    assert result.shape == {}
    assert result.summary is None

"""Valhalla render honesty: never show a route we did not get.

Tests that render.py returns honest status codes for:
- empty geometry → honest non-usable status
- unreachable pair → treated as unreachable by the helper
- timeout → service-unavailable
- maneuvers without instructions reported

No network — valhalla_client._request_with_retry is monkeypatched.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import valhalla_client as vc
from agent.models import Candidate
from agent.planner import render


def _c(id: int, lat: float, lon: float, name: str) -> Candidate:
    return Candidate(
        id=id,
        name=name,
        category="замок",
        lat=lat,
        lon=lon,
        rrf_score=1.0,
        relevance=1.0,
        visit_minutes_db=30,
        town="Гродно",
        district="Гродненский район",
    )


def test_empty_geometry_returns_honest_status(monkeypatch):
    """Empty geometry should return empty_geometry status, not a usable route."""
    route = [_c(0, 53.6791, 23.8216, "Старый замок"), _c(1, 53.6849, 23.8310, "Новый замок")]

    def fake_route_through(locations, costing, language):
        return vc.RouteResult(
            status=vc.RouteStatus.EMPTY_GEOMETRY,
            shape={},
            summary=None,
            maneuvers=None,
            language=language,
        )

    monkeypatch.setattr(render, "route_through", fake_route_through)

    shape, summary, status = render.render(route, costing="pedestrian", locale="ru")

    assert status == "empty_geometry"
    assert shape == {}
    assert summary == {}


def test_unreachable_pair_treated_by_helper(monkeypatch):
    """Unreachable pair should be handled by is_unreachable_time helper."""
    # Test the helper function directly
    from agent import constants

    assert vc.is_unreachable_time(float(constants.UNREACHABLE_S))
    assert not vc.is_unreachable_time(100.0)
    assert not vc.is_unreachable_time(0.0)


def test_timeout_returns_service_unavailable(monkeypatch):
    """Timeout should return service_unavailable status."""
    route = [_c(0, 53.6791, 23.8216, "Старый замок"), _c(1, 53.6849, 23.8310, "Новый замок")]

    def fake_route_through(locations, costing, language):
        return vc.RouteResult(
            status=vc.RouteStatus.SERVICE_UNAVAILABLE,
            shape={},
            summary=None,
            maneuvers=None,
            language=language,
        )

    monkeypatch.setattr(render, "route_through", fake_route_through)

    shape, summary, status = render.render(route, costing="pedestrian", locale="ru")

    assert status == "service_unavailable"
    assert shape == {}
    assert summary == {}


def test_maneuvers_without_instructions_reported(monkeypatch):
    """Maneuvers without instructions should be reported as missing_maneuver_data."""
    route = [_c(0, 53.6791, 23.8216, "Старый замок"), _c(1, 53.6849, 23.8310, "Новый замок")]

    def fake_route_through(locations, costing, language):
        return vc.RouteResult(
            status=vc.RouteStatus.USABLE,
            shape={"type": "LineString", "coordinates": [[23.8216, 53.6791], [23.8310, 53.6849]]},
            summary={"length": 1.2, "time": 900.0},
            maneuvers=[
                {"type": "depart", "length": 0.0, "time": 0.0},  # missing instruction
                {"instruction": "Turn right", "length": 100.0, "time": 60.0, "type": "turn"},
            ],
            language=language,
        )

    monkeypatch.setattr(render, "route_through", fake_route_through)

    shape, summary, status = render.render(route, costing="pedestrian", locale="ru")

    assert status == "missing_maneuver_data"
    assert shape["type"] == "LineString"
    assert summary["length"] == 1.2


def test_locale_mismatch_returns_honest_status(monkeypatch):
    """Locale mismatch should return locale_mismatch status."""
    route = [_c(0, 53.6791, 23.8216, "Старый замок"), _c(1, 53.6849, 23.8310, "Новый замок")]

    def fake_route_through(locations, costing, language):
        # Request "ru" but response comes back as "en"
        return vc.RouteResult(
            status=vc.RouteStatus.USABLE,
            shape={"type": "LineString", "coordinates": [[23.8216, 53.6791], [23.8310, 53.6849]]},
            summary={"length": 1.2, "time": 900.0},
            maneuvers=[
                {"instruction": "Turn right", "length": 100.0, "time": 60.0, "type": "turn"}
            ],
            language="en",  # mismatch
        )

    monkeypatch.setattr(render, "route_through", fake_route_through)

    shape, summary, status = render.render(route, costing="pedestrian", locale="ru")

    assert status == "locale_mismatch"


def test_missing_required_maneuver_fields_reported(monkeypatch):
    """Missing required maneuver fields (length, time, type) should be reported."""
    route = [_c(0, 53.6791, 23.8216, "Старый замок"), _c(1, 53.6849, 23.8310, "Новый замок")]

    def fake_route_through(locations, costing, language):
        return vc.RouteResult(
            status=vc.RouteStatus.USABLE,
            shape={"type": "LineString", "coordinates": [[23.8216, 53.6791], [23.8310, 53.6849]]},
            summary={"length": 1.2, "time": 900.0},
            maneuvers=[
                {"instruction": "Turn right"},  # missing length, time, type
            ],
            language=language,
        )

    monkeypatch.setattr(render, "route_through", fake_route_through)

    shape, summary, status = render.render(route, costing="pedestrian", locale="ru")

    assert status == "missing_maneuver_data"


def test_usable_route_with_valid_maneuvers_and_locale(monkeypatch):
    """A valid route with correct locale and complete maneuvers should return usable."""
    route = [_c(0, 53.6791, 23.8216, "Старый замок"), _c(1, 53.6849, 23.8310, "Новый замок")]

    def fake_route_through(locations, costing, language):
        return vc.RouteResult(
            status=vc.RouteStatus.USABLE,
            shape={"type": "LineString", "coordinates": [[23.8216, 53.6791], [23.8310, 53.6849]]},
            summary={"length": 1.2, "time": 900.0},
            maneuvers=[
                {"instruction": "Turn right", "length": 100.0, "time": 60.0, "type": "turn"}
            ],
            language=language,  # matches requested locale
        )

    monkeypatch.setattr(render, "route_through", fake_route_through)

    shape, summary, status = render.render(route, costing="pedestrian", locale="ru")

    assert status == "usable"
    assert shape["type"] == "LineString"
    assert summary["length"] == 1.2


def test_no_route_exists_status(monkeypatch):
    """When no route exists, should return no_route_exists status."""
    route = [_c(0, 53.6791, 23.8216, "Старый замок"), _c(1, 53.6849, 23.8310, "Новый замок")]

    def fake_route_through(locations, costing, language):
        return vc.RouteResult(
            status=vc.RouteStatus.NO_ROUTE_EXISTS,
            shape={},
            summary=None,
            maneuvers=None,
            language=language,
        )

    monkeypatch.setattr(render, "route_through", fake_route_through)

    shape, summary, status = render.render(route, costing="pedestrian", locale="ru")

    assert status == "no_route_exists"
    assert shape == {}
    assert summary == {}


def test_single_point_returns_no_route_exists(monkeypatch):
    """A route with fewer than 2 points should return no_route_exists."""
    route = [_c(0, 53.6791, 23.8216, "Старый замок")]

    shape, summary, status = render.render(route, costing="pedestrian", locale="ru")

    assert status == "no_route_exists"
    assert shape == {}
    assert summary == {}

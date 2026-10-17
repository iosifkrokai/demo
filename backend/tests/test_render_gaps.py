"""What the drawn line does when the engine cannot draw all of it."""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.errors import UpstreamUnavailableError
from routing import render
from routing.valhalla import http as valhalla_http
from routing.valhalla.route import best_route, route_through
from routing.valhalla.types import RouteStatus

LEG_SHAPE = "????"

POINTS = [
    {"lat": 53.677, "lon": 23.822},
    {"lat": 53.678, "lon": 23.825},
    {"lat": 53.679, "lon": 23.828},
]


def _trip(seconds: float, length: float) -> dict[str, Any]:
    return {
        "trip": {
            "legs": [{"shape": LEG_SHAPE, "maneuvers": []}],
            "summary": {"time": seconds, "length": length},
        }
    }


def _stub(monkeypatch: pytest.MonkeyPatch, route_answer: Any) -> list[dict[str, Any]]:
    """Answer /locate as a snappable edge and hand /route to `route_answer`."""
    sent: list[dict[str, Any]] = []

    def answer(method: str, url: str, *, params: dict[str, Any], timeout: float, **_: Any) -> Any:
        payload = json.loads(params["json"])
        if url.endswith("/locate"):
            return [
                {"edges": [{"correlated_lat": loc["lat"], "correlated_lon": loc["lon"]}]}
                for loc in payload["locations"]
            ]
        sent.append(payload)
        return route_answer(payload)

    monkeypatch.setattr(valhalla_http, "request_with_retry", answer)
    return sent


def test_a_stop_the_engine_cannot_reach_is_dropped_for_the_cheapest_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every single removal is worth asking about: which stop is the problem is the
    engine's answer, and the first one tried is not the one to lose.
    """

    def route_answer(payload: dict[str, Any]) -> Any:
        stops = payload["locations"]
        if len(stops) == 3:
            raise UpstreamUnavailableError("no path could be found")
        missing = [
            i
            for i, point in enumerate(POINTS)
            if all(abs(loc["lat"] - point["lat"]) > 1e-9 for loc in stops)
        ]
        return _trip(100.0 * (len(POINTS) - missing[0]), 1.0)

    sent = _stub(monkeypatch, route_answer)

    result = route_through([dict(p) for p in POINTS], costing="pedestrian")

    assert len([p for p in sent if len(p["locations"]) == 2]) == 3, "all three removals tried"
    assert result.status == RouteStatus.USABLE
    assert result.summary is not None
    assert float(result.summary["time"]) == pytest.approx(100.0), "the cheapest removal was kept"


def test_a_leg_that_cannot_be_drawn_makes_the_line_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Half a line is not a line: the status says so, so nobody reports it as whole."""
    blocked = 53.679

    def route_answer(payload: dict[str, Any]) -> Any:
        stops = payload["locations"]
        if len(stops) >= 3 or any(abs(loc["lat"] - blocked) < 1e-9 for loc in stops):
            raise UpstreamUnavailableError("no path could be found")
        return _trip(600.0, 1.0)

    _stub(monkeypatch, route_answer)

    shape, summary, status = render.render(
        [
            _candidate(53.677, 23.822),
            _candidate(53.678, 23.825),
            _candidate(blocked, 23.828),
            _candidate(53.680, 23.831),
        ],
        costing="pedestrian",
    )

    assert status == "partial"
    assert shape.get("coordinates"), "the legs that could be drawn are still drawn"
    assert float(summary["time"]) == pytest.approx(600.0), "only the drawn leg is counted"


def test_the_whole_line_reports_itself_as_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, lambda payload: _trip(300.0, 1.0))

    shape, _, status = render.render(
        [_candidate(53.677, 23.822), _candidate(53.678, 23.825)], costing="pedestrian"
    )

    assert status == "usable"
    assert shape.get("coordinates")


def _candidate(lat: float, lon: float) -> Any:
    from routing.models import Candidate

    return Candidate(id=1, name="точка", category="музей", lat=lat, lon=lon)


def test_the_quickest_of_the_offered_routes_is_the_one_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The engine answers with its own route and up to two others; the seconds we
    promise are the tourist's, so the quickest of the three is the one to keep.
    """

    def route_answer(payload: dict[str, Any]) -> Any:
        assert payload["alternates"] == 2, "the alternates have to be asked for"
        body = _trip(500.0, 0.8)
        body["alternates"] = [_trip(300.0, 0.5), _trip(700.0, 1.1)]
        return body

    _stub(monkeypatch, route_answer)

    result = best_route([{"lat": 53.677, "lon": 23.822}, {"lat": 53.678, "lon": 23.825}])

    assert result.status == RouteStatus.USABLE
    assert result.summary is not None
    assert float(result.summary["time"]) == pytest.approx(300.0)
    assert float(result.summary["length"]) == pytest.approx(0.5)


def test_a_slower_alternate_does_not_displace_the_main_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def route_answer(payload: dict[str, Any]) -> Any:
        body = _trip(200.0, 0.4)
        body["alternates"] = [_trip(500.0, 0.9)]
        return body

    _stub(monkeypatch, route_answer)

    result = best_route([{"lat": 53.677, "lon": 23.822}, {"lat": 53.678, "lon": 23.825}])

    assert result.summary is not None
    assert float(result.summary["time"]) == pytest.approx(200.0)

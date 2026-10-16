"""What the snap cache remembers, and whose answer it remembers it from."""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.errors import UpstreamUnavailableError
from routing.valhalla import http as valhalla_http, snap

SNAPPED = {
    "pedestrian": (53.678730, 23.821894),
    "bicycle": (53.677000, 23.824000),
    "auto": (53.677500, 23.823500),
}


@pytest.fixture(autouse=True)
def _empty_cache() -> None:
    snap.SNAP_CACHE.clear()


def _answers_by_costing(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Answer every /locate with a point that depends on the costing asked for."""
    sent: list[dict[str, Any]] = []

    def answer(method: str, url: str, *, params: dict[str, Any], timeout: float, **_: Any) -> Any:
        payload = json.loads(params["json"])
        sent.append(payload)
        costing = payload["costing"]
        return [
            {"edges": [{"correlated_lat": lat, "correlated_lon": lon}]}
            for lat, lon in [SNAPPED[costing]] * len(payload["locations"])
        ]

    monkeypatch.setattr(valhalla_http, "request_with_retry", answer)
    return sent


def _refuse(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def refuse(method: str, url: str, *, params: dict[str, Any], timeout: float, **_: Any) -> Any:
        sent.append(json.loads(params["json"]))
        raise UpstreamUnavailableError("captured")

    monkeypatch.setattr(valhalla_http, "request_with_retry", refuse)
    return sent


def test_the_same_point_snaps_differently_for_each_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/locate` answers per costing — one point can sit on a footway and, for a
    bicycle, on the street behind it.
    """
    point = {"lat": 53.6845, "lon": 23.8318}
    sent = _answers_by_costing(monkeypatch)

    walked = snap.snap_locations([dict(point)], costing="pedestrian")[0]
    cycled = snap.snap_locations([dict(point)], costing="bicycle")[0]

    assert (walked["lat"], walked["lon"]) == SNAPPED["pedestrian"]
    assert (cycled["lat"], cycled["lon"]) == SNAPPED["bicycle"]
    assert [payload["costing"] for payload in sent] == ["pedestrian", "bicycle"]


def test_the_cache_remembers_the_profile_it_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """`car` is `auto` on the wire, and both are the same cache entry."""
    point = {"lat": 53.6845, "lon": 23.8318}
    _answers_by_costing(monkeypatch)

    snap.snap_locations([dict(point)], costing="car")
    snap.snap_locations([dict(point)], costing="auto")
    snap.snap_locations([dict(point)], costing="bicycle")

    assert len(snap.SNAP_CACHE) == 2, "car and auto are one costing, bicycle another"


def test_a_whole_plan_is_snapped_in_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    points = [{"lat": 53.678 + i / 1000, "lon": 23.827 + i / 1000} for i in range(8)]
    sent = _answers_by_costing(monkeypatch)

    out = snap.snap_locations(points, costing="pedestrian")

    assert len(sent) == 1, "eight points, one /locate"
    assert [loc["lat"] for loc in sent[0]["locations"]] == [p["lat"] for p in points]
    assert len(out) == 8


def test_a_repeated_point_rides_along_once(monkeypatch: pytest.MonkeyPatch) -> None:
    point = {"lat": 53.678, "lon": 23.827}
    sent = _answers_by_costing(monkeypatch)

    out = snap.snap_locations([dict(point), dict(point)], costing="pedestrian")

    assert len(sent[0]["locations"]) == 1
    assert out[0] == out[1]


def test_a_point_already_known_is_not_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    points = [{"lat": 53.678, "lon": 23.827}, {"lat": 53.679, "lon": 23.828}]
    sent = _answers_by_costing(monkeypatch)

    snap.snap_locations(points, costing="pedestrian")
    snap.snap_locations(points, costing="pedestrian")

    assert len(sent) == 1


def test_a_batch_too_big_for_one_call_is_split(monkeypatch: pytest.MonkeyPatch) -> None:
    over = snap.LOCATE_BATCH_MAX + 3
    points = [{"lat": 53.6 + i / 1000, "lon": 23.8 + i / 1000} for i in range(over)]
    sent = _answers_by_costing(monkeypatch)

    snap.snap_locations(points, costing="pedestrian")

    assert [len(payload["locations"]) for payload in sent] == [snap.LOCATE_BATCH_MAX, 3]


def test_a_refused_call_keeps_the_point_where_the_tourist_put_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A service that cannot answer is not a reason to move the stop."""
    points = [{"lat": 53.678, "lon": 23.827}, {"lat": 53.679, "lon": 23.828}]
    sent = _refuse(monkeypatch)

    out = snap.snap_locations(points, costing="pedestrian")

    assert out == points
    snap.snap_locations(points, costing="pedestrian")
    assert len(sent) == 1, "a refusal is remembered, not retried on every call"


def test_the_snappable_check_asks_once_for_the_whole_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locations = [{"lat": 53.678 + i / 1000, "lon": 23.827 + i / 1000} for i in range(4)]
    sent = _answers_by_costing(monkeypatch)

    kept = snap.drop_unsnappable(locations, "pedestrian", None)

    assert len(sent) == 1
    assert len(kept) == 4
    assert sent[0]["locations"][0]["radius"] == snap.LOCATE_RADIUS_M


def test_a_point_with_no_edge_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    locations = [{"lat": 53.678, "lon": 23.827}, {"lat": 53.679, "lon": 23.828}]

    def answer(method: str, url: str, *, params: dict[str, Any], timeout: float, **_: Any) -> Any:
        return [{"edges": [{"correlated_lat": 53.678, "correlated_lon": 23.827}]}, {"edges": []}]

    monkeypatch.setattr(valhalla_http, "request_with_retry", answer)
    kept = snap.drop_unsnappable(locations, "pedestrian", None)

    assert kept == [locations[0]]


def test_an_unreachable_engine_keeps_every_point(monkeypatch: pytest.MonkeyPatch) -> None:
    locations = [{"lat": 53.678, "lon": 23.827}, {"lat": 53.679, "lon": 23.828}]
    _refuse(monkeypatch)

    assert snap.drop_unsnappable(locations, "pedestrian", None) == locations

"""A stop on a disconnected road island must not kill the whole route."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from contracts.planner import Candidate, CostMatrix
from core.errors import UpstreamUnavailable
from domain import constants
from infra import valhalla_client as vc
from planner import render as render_mod
from planner.cost import prune_unroutable_stops

ISLAND = (53.007611, 23.917041)
MAINLAND = (53.290892, 23.932859)
ISLAND_NEIGHBOUR = (53.017611, 23.917041)


def _candidate(pid: int, lat: float, lon: float, name: str = "p") -> Candidate:
    return Candidate(id=pid, name=name, category="костёл", lat=lat, lon=lon)


def _no_path_error() -> UpstreamUnavailable:
    body = '{"error_code":442,"error":"No path could be found for input"}'
    return UpstreamUnavailable(
        f"valhalla GET http://valhalla/route failed after retries: server error: {body}"
    )


def test_null_matrix_cell_is_the_unreachable_sentinel(monkeypatch):
    def fake_request(method, url, *, params, timeout):
        return {
            "sources_to_targets": [
                [
                    {"from_index": 0, "to_index": 0, "time": 0.0},
                    {"from_index": 0, "to_index": 1, "time": None},
                ],
                [
                    {"from_index": 1, "to_index": 0, "time": None},
                    {"from_index": 1, "to_index": 1, "time": 0.0},
                ],
            ]
        }

    monkeypatch.setattr(vc, "_request_with_retry", fake_request)
    monkeypatch.setattr(vc, "_chunks_safe", lambda n_src, n_tgt: True)

    matrix = vc.time_matrix(
        [{"lat": MAINLAND[0], "lon": MAINLAND[1]}, {"lat": ISLAND[0], "lon": ISLAND[1]}],
        [{"lat": MAINLAND[0], "lon": MAINLAND[1]}, {"lat": ISLAND[0], "lon": ISLAND[1]}],
    )

    assert matrix[0][1] == float(constants.UNREACHABLE_S)
    assert matrix[1][0] == float(constants.UNREACHABLE_S)
    assert matrix[0][0] == 0.0 and matrix[1][1] == 0.0


def test_prune_drops_the_stop_after_an_unroutable_leg():
    a = _candidate(1, *MAINLAND, name="Волковыск")
    b = _candidate(2, *ISLAND, name="каплица на острове")
    c = _candidate(3, 53.6787, 23.8279, name="Гродно")

    a_b = float(constants.UNREACHABLE_S)
    cost = CostMatrix(
        walk_seconds=[[0.0, a_b, 600.0], [a_b, 0.0, 700.0], [600.0, 700.0, 0.0]],
        visit_minutes=[10, 10, 10],
        indices=[0, 1, 2],
    )

    kept, dropped = prune_unroutable_stops([a, b, c], [a, b, c], cost)

    assert [x.id for x in kept] == [1, 3]
    assert [x.id for x in dropped] == [2]


def test_prune_leaves_a_healthy_tour_alone():
    a = _candidate(1, 53.6787, 23.8279)
    b = _candidate(2, 53.6808, 23.8305)
    c = _candidate(3, 53.6813, 23.8278)
    cost = CostMatrix(
        walk_seconds=[[0.0, 300.0, 400.0], [300.0, 0.0, 250.0], [400.0, 250.0, 0.0]],
        visit_minutes=[10, 10, 10],
        indices=[0, 1, 2],
    )

    kept, dropped = prune_unroutable_stops([a, b, c], [a, b, c], cost)

    assert [x.id for x in kept] == [1, 2, 3]
    assert dropped == []


def test_render_falls_back_to_legs_when_the_tour_is_refused(monkeypatch):
    calls: list[list[dict]] = []

    def fake_route_through(locations, costing="pedestrian", language="ru", timeout=None):
        locs = list(locations)
        calls.append(locs)
        if len(locs) > 2:
            return vc.RouteResult(
                status=vc.RouteStatus.NO_ROUTE_EXISTS,
                shape={},
                summary=None,
                maneuvers=None,
                language=language,
            )
        return vc.RouteResult(
            status=vc.RouteStatus.USABLE,
            shape={"type": "LineString", "coordinates": [[23.9, 53.0], [23.91, 53.01]]},
            summary={"length": 1.5, "time": 300.0},
            maneuvers=None,
            language=language,
        )

    monkeypatch.setattr(render_mod, "route_through", fake_route_through)
    route = [
        _candidate(1, 53.0, 23.9),
        _candidate(2, 53.01, 23.91),
        _candidate(3, 53.02, 23.92),
    ]

    shape, summary, status = render_mod.render(route, costing="auto")

    assert len(calls) == 3, "one whole-tour attempt + two legs"
    assert status == "usable", "the per-leg fallback still yields a drawable route"
    assert len(shape["coordinates"]) == 4
    assert summary["length"] == 3.0
    assert summary["time"] == 600.0


def test_no_path_400_is_classified_as_a_route_failure():
    assert vc._is_route_failure(_no_path_error())
    assert not vc._is_route_failure(
        UpstreamUnavailable("valhalla GET /route failed after retries: [Errno 111] Connection refused")
    )


def test_route_through_drops_the_island_stop_instead_of_raising(monkeypatch):
    routed_sizes: list[int] = []

    def fake_request(method, url, *, params, timeout):
        payload = json.loads(params["json"])
        locs = payload["locations"]
        routed_sizes.append(len(locs))
        if any(abs(loc["lat"] - ISLAND[0]) < 1e-4 and abs(loc["lon"] - ISLAND[1]) < 1e-4 for loc in locs):
            raise _no_path_error()
        return {
            "trip": {
                "summary": {"length": 10.0, "time": 600.0},
                "legs": [{"shape": "yzocbAqzc_hB"}],
            }
        }

    monkeypatch.setattr(vc, "_request_with_retry", fake_request)
    monkeypatch.setattr(vc, "snap_locations", lambda locs, costing, timeout=None: list(locs))
    monkeypatch.setattr(vc, "_snappable", lambda loc, costing, timeout: True)

    result = vc.route_through(
        [
            {"lat": MAINLAND[0], "lon": MAINLAND[1], "type": "break"},
            {"lat": ISLAND[0], "lon": ISLAND[1], "type": "via"},
            {"lat": 53.6787, "lon": 23.8279, "type": "break"},
        ],
        costing="auto",
    )

    assert result.status == vc.RouteStatus.USABLE
    assert result.shape, "the tour still gets drawn without the island stop"
    assert routed_sizes[0] == 3, "first try has all three stops"
    assert 2 in routed_sizes[1:], "then the island stop is dropped and it routes"

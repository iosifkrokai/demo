"""Matrix resilience: a chunk 500 must not become a 503 for the whole request."""

from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.errors import UpstreamUnavailable
from domain import constants
from infra import valhalla_client as vc

A = {"lat": 53.6791, "lon": 23.8216}
B = {"lat": 53.6849, "lon": 23.8310}
C = {"lat": 53.6787, "lon": 23.8279}

LABEL_500 = (
    "valhalla GET http://valhalla/sources_to_targets failed after retries: "
    'server error: {"error_code":499,"error":"Unknown: Could not find candidate edge '
    'used for destination label"}'
)


def _radius(params: dict) -> int:
    payload = json.loads(params["json"])
    return payload.get("sources", [{}])[0].get("radius")


def _matrix_response(sources, targets, radius):
    return {
        "sources_to_targets": [
            [{"time": float(radius + i + j)} for j in range(len(targets))]
            for i in range(len(sources))
        ]
    }


def test_small_matrix_retries_with_a_wider_radius(monkeypatch):
    radii: list[int] = []

    def fake(method, url, *, params, timeout):
        r = _radius(params)
        radii.append(r)
        if r == vc.LOCATION_SNAP_RADIUS_M:
            raise UpstreamUnavailable(LABEL_500)
        payload = json.loads(params["json"])
        return _matrix_response(payload["sources"], payload["targets"], r)

    monkeypatch.setattr(vc, "_request_with_retry", fake)
    m = vc.time_matrix([A, B, C], [A, B, C])

    assert len(m) == 3 and len(m[0]) == 3
    assert radii == [vc.ROUTE_SNAP_RADII_M[0], vc.ROUTE_SNAP_RADII_M[1]]
    assert m[0][0] == 0.0, "diagonal stays 0"


def test_matrix_falls_back_to_per_pair_route(monkeypatch):
    """Every radius fails → per-pair /route fills the matrix (no exception)."""
    calls: list[str] = []

    def fake(method, url, *, params, timeout):
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("/sources_to_targets"):
            raise UpstreamUnavailable(LABEL_500)
        return {"trip": {"summary": {"time": 120.0}}}

    monkeypatch.setattr(vc, "_request_with_retry", fake)
    m = vc.time_matrix([A, B], [A, B])

    assert len(calls) == len(vc.ROUTE_SNAP_RADII_M) + 2, "4 matrix attempts + 2 pairs"
    assert m[0][0] == 0.0, "same location object → diagonal 0, not a /route call"
    assert m[0][1] == 120.0
    assert m[1][0] == 120.0


def test_unroutable_pair_becomes_the_unreachable_sentinel(monkeypatch):
    def fake(method, url, *, params, timeout):
        raise UpstreamUnavailable(LABEL_500)

    monkeypatch.setattr(vc, "_request_with_retry", fake)
    m = vc.time_matrix([A, B], [A, B])

    assert m[0][1] == float(constants.UNREACHABLE_S), "unreachable pair, not a 503"
    assert math.isfinite(m[0][1])


def test_transport_failure_is_unknown_not_unreachable(monkeypatch):
    """A timeout must not be turned into "no path exists".
    It must surface as UNKNOWN_S instead — the plan degrades, no stop is dropped.
    """

    def fake(method, url, *, params, timeout):
        raise UpstreamUnavailable(
            "valhalla GET /sources_to_targets failed after retries: connect timeout"
        )

    monkeypatch.setattr(vc, "_request_with_retry", fake)
    m = vc.time_matrix([A, B], [A, B])
    assert math.isnan(m[0][1]), "a transport failure is unknown, not unreachable"
    assert m[0][1] != float(constants.UNREACHABLE_S)

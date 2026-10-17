"""What the matrix asks the engine for — the shape of the question, not its answer."""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.errors import UpstreamUnavailableError
from routing.valhalla import http as valhalla_http, matrix

PAIR = [{"lat": 53.676148, "lon": 23.836303}, {"lat": 53.676766, "lon": 23.837767}]


def _payloads(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every payload the matrix sends, in order. Each request is then refused, so
    the whole escalation runs and the matrix degrades rather than hanging.
    """
    sent: list[dict[str, Any]] = []

    def capture(method: str, url: str, *, params: dict[str, Any], timeout: float, **_: Any) -> Any:
        sent.append(json.loads(params["json"]))
        raise UpstreamUnavailableError("captured")

    monkeypatch.setattr(valhalla_http, "request_with_retry", capture)
    return sent


def _radii(payload: dict[str, Any]) -> list[int | None]:
    locations = payload.get("sources") or payload.get("locations") or []
    return [location.get("radius") for location in locations]


def test_the_matrix_asks_without_a_snap_radius_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """A radius lets the engine pick the cheapest edge, folding two places on one street
    onto a single edge; the default must answer what `/route` answers.
    """
    sent = _payloads(monkeypatch)

    matrix.time_matrix(PAIR, PAIR, "pedestrian", timeout=1.0)

    assert sent, "the matrix never asked"
    assert _radii(sent[0]) == [None, None]


def test_a_location_the_engine_refuses_is_asked_again_wider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wide radii are still there for a location that is genuinely off the
    network — as a last resort, in this order, and nothing wider in between.
    """
    sent = _payloads(monkeypatch)

    matrix.time_matrix(PAIR, PAIR, "pedestrian", timeout=1.0)

    chunk_radii = [_radii(payload) for payload in sent if "sources" in payload]
    assert chunk_radii == [[None, None], [100, 100], [500, 500], [2000, 2000], [5000, 5000]]

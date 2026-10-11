"""The valhalla package split must neither fork the snap cache nor bind the seam.

The old ``valhalla_client`` module was patched through private names. After the
split those seams live on the submodules; these tests prove the two failure modes
that would let the split look fine while testing nothing:
- a duplicated snap cache (more HTTP calls, no error), and
- a transport bound at import (a patch that silently stops affecting the matrix).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from planner.valhalla import http, matrix, snap


def test_snap_cache_hits_the_network_once(monkeypatch):
    """Two snaps of the same coordinate must ask Valhalla once, not twice."""
    snap.SNAP_CACHE.clear()
    calls: list[str] = []

    def fake(method: str, url: str, *, params: dict, timeout: float) -> list[dict]:
        calls.append(url)
        return [{"edges": [{"correlated_lat": 53.5, "correlated_lon": 23.5}]}]

    monkeypatch.setattr(http, "request_with_retry", fake)
    loc = {"lat": 53.6791, "lon": 23.8216}

    first = snap.snap_locations([loc])
    second = snap.snap_locations([loc])

    assert len(calls) == 1, "the same coordinate must be snapped once, not twice"
    assert first == second == [{"lat": 53.5, "lon": 23.5}]


def test_matrix_reads_the_transport_seam_off_the_module(monkeypatch):
    """Patching the transport in planner.valhalla.http must change time_matrix.

    If matrix.py had bound ``from .http import request_with_retry`` at import,
    this patch would be inert and time_matrix would reach the real network.
    """
    calls: list[str] = []

    def fake(method: str, url: str, *, params: dict, timeout: float) -> dict:
        calls.append(url)
        return {"sources_to_targets": [[{"time": 42.0}]]}

    monkeypatch.setattr(http, "request_with_retry", fake)

    result = matrix.time_matrix(
        [{"lat": 53.6791, "lon": 23.8216}],
        [{"lat": 53.6849, "lon": 23.8310}],
    )

    assert result == [[42.0]], "the patched transport answer must reach the matrix"
    assert calls, "the matrix must go through the module-level transport seam"


def test_matrix_does_not_bind_the_transport_at_import():
    """The seam stays on http; matrix must not keep a private copy of it."""
    assert hasattr(http, "request_with_retry")
    assert not hasattr(matrix, "request_with_retry")

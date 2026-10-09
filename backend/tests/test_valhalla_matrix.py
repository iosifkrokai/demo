"""Tests for valhalla_client time_matrix chunking.

No network is used — _request_with_retry is monkeypatched.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import valhalla_client as vc
from agent.errors import UpstreamUnavailable

# Fake response builder

def fake_matrix_response(sources: list[dict], targets: list[dict]) -> dict:
    """Deterministic matrix: cell[global_src][global_tgt] = global_src*100 + global_tgt.

    Uses _src_idx / _tgt_idx annotations (added by time_matrix internals) to
    obtain global indices. Falls back to local indices for any dict that lacks
    them (e.g. a bare {lat, lon} dict passed directly).

    Diagonal cells (same object identity, src is tgt) are 0.0 to match Valhalla
    behaviour for same-location pairs.
    """
    rows = []
    for si, src in enumerate(sources):
        row = []
        for ti, tgt in enumerate(targets):
            if src is tgt:
                row.append({"time": 0.0})
            else:
                gs = src.get("_src_idx", si)
                gt = tgt.get("_tgt_idx", ti)
                row.append({"time": float(gs * 100 + gt)})
        rows.append(row)
    return {"sources_to_targets": rows}


# Test helpers

def get_shape_from_call(call: dict) -> tuple[int, int]:
    """Extract (n_sources, n_targets) from a recorded call dict."""
    payload = json.loads(call["kwargs"]["params"]["json"])
    return len(payload["sources"]), len(payload["targets"])


def assert_no_unsafe_shapes(calls: list) -> None:
    """Fail if any recorded call has len(sources) >= 6 AND len(targets) >= 7."""
    for call in calls:
        n_src, n_tgt = get_shape_from_call(call)
        assert not (n_src >= 6 and n_tgt >= 7), (
            f"Unsafe shape emitted: {n_src}x{n_tgt}"
        )


def assert_matrix_correct(
    result: list[list[float]],
    n_src: int,
    n_tgt: int,
) -> None:
    """Assert result[i][j] == i*100 + j (the expected value for global indices)."""
    assert len(result) == n_src, f"Wrong row count: {len(result)} != {n_src}"
    for i in range(n_src):
        assert len(result[i]) == n_tgt, (
            f"Wrong col count in row {i}: {len(result[i])} != {n_tgt}"
        )
        for j in range(n_tgt):
            expected = float(i * 100 + j)
            assert result[i][j] == expected, (
                f"[{i}][{j}] = {result[i][j]}, expected {expected}"
            )


# Test cases

def _make_pts(n: int) -> list[dict]:
    """Make n dummy {lat, lon} points."""
    return [{"lat": float(i), "lon": float(i)} for i in range(n)]


def test_small_matrix_one_call():
    """A 5x5 matrix should be one HTTP call (fast path)."""
    sources = _make_pts(5)
    targets = _make_pts(5)

    calls = []
    def fake_request(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        payload = json.loads(kwargs["params"]["json"])
        return fake_matrix_response(payload["sources"], payload["targets"])

    with patch.object(vc, "_request_with_retry", side_effect=fake_request):
        result = vc.time_matrix(sources, targets)

    assert len(calls) == 1
    assert_no_unsafe_shapes(calls)
    assert_matrix_correct(result, 5, 5)


def test_12x12_is_chunked():
    """A 12x12 matrix must be split into safe chunks, not sent as one call."""
    sources = _make_pts(12)
    targets = _make_pts(12)

    calls = []
    def fake_request(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        payload = json.loads(kwargs["params"]["json"])
        return fake_matrix_response(payload["sources"], payload["targets"])

    with patch.object(vc, "_request_with_retry", side_effect=fake_request):
        result = vc.time_matrix(sources, targets)

    assert_no_unsafe_shapes(calls)
    assert_matrix_correct(result, 12, 12)
    # ceil(12/5) * ceil(12/5) = 3 * 3 = 9 chunks
    assert len(calls) == 9, f"Expected 9 chunks, got {len(calls)}"


def test_rectangular_chunking():
    """12 sources x 8 targets: 3 row chunks x 2 col chunks = 6 calls."""
    sources = _make_pts(12)
    targets = _make_pts(8)

    calls = []
    def fake_request(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        payload = json.loads(kwargs["params"]["json"])
        return fake_matrix_response(payload["sources"], payload["targets"])

    with patch.object(vc, "_request_with_retry", side_effect=fake_request):
        result = vc.time_matrix(sources, targets)

    assert_no_unsafe_shapes(calls)
    assert_matrix_correct(result, 12, 8)
    # ceil(12/5) * ceil(8/6) = 3 * 2 = 6
    assert len(calls) == 6


def test_fallback_on_upstream_error():
    """If a chunk raises UpstreamUnavailable, the matrix is still complete."""
    sources = _make_pts(12)
    targets = _make_pts(12)

    call_count = [0]

    def fake_request(*args, **kwargs):
        call_count[0] += 1
        url = kwargs.get("url") or (args[1] if len(args) > 1 else "")

        # First call fails (UpstreamUnavailable), subsequent safe-chunk calls succeed.
        if call_count[0] == 1:
            raise UpstreamUnavailable("simulated 500")

        if "route" in url:
            return {"trip": {"summary": {"time": 99.0}}}

        payload = json.loads(kwargs["params"]["json"])
        return fake_matrix_response(payload["sources"], payload["targets"])

    with patch.object(vc, "_request_with_retry", side_effect=fake_request):
        result = vc.time_matrix(sources, targets)

    assert len(result) == 12
    assert all(len(row) == 12 for row in result)
    # Should have completed despite the first failure.
    assert call_count[0] > 1


def test_diagonal_zero_for_identical_coordinates():
    """Diagonal of square matrix should be 0.0 without any HTTP call for it.

    When sources and targets are the same objects, diagonal is 0.
    """
    sources = _make_pts(3)
    targets = list(sources)  # shallow copy — same dict objects

    calls = []
    def fake_request(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        payload = json.loads(kwargs["params"]["json"])
        return fake_matrix_response(payload["sources"], payload["targets"])

    with patch.object(vc, "_request_with_retry", side_effect=fake_request):
        result = vc.time_matrix(sources, targets, costing="pedestrian")

    # Diagonal must be 0 (sources[i] is targets[i] — same object identity).
    for i in range(3):
        assert result[i][i] == 0.0, f"Diagonal [{i}][{i}] should be 0.0, got {result[i][i]}"

    # With 3x3 (fast path) there should be exactly 1 call.
    assert len(calls) == 1


def test_constants_recorded():
    """Every shape above 5x5 is split: a live 6x6 has 500'd on this box."""
    assert vc.MATRIX_MAX_SOURCES == 5
    assert vc.MATRIX_MAX_TARGETS == 5

    # Safe: both dimensions fit in one request.
    assert vc._chunks_safe(5, 5) is True
    assert vc._chunks_safe(3, 3) is True
    assert vc._chunks_safe(1, 5) is True
    assert vc._chunks_safe(5, 1) is True

    # Unsafe: either dimension above the cap gets chunked, including 6x6,
    # which is what "замки Гродно" actually sent before this rule.
    assert vc._chunks_safe(6, 6) is False
    assert vc._chunks_safe(5, 6) is False
    assert vc._chunks_safe(6, 5) is False
    assert vc._chunks_safe(6, 7) is False
    assert vc._chunks_safe(7, 7) is False
    assert vc._chunks_safe(12, 12) is False


def test_6x7_is_chunked_not_one_call():
    """6x7 triggers the bug — must be chunked into multiple safe calls."""
    sources = _make_pts(6)
    targets = _make_pts(7)

    calls = []
    def fake_request(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        payload = json.loads(kwargs["params"]["json"])
        return fake_matrix_response(payload["sources"], payload["targets"])

    with patch.object(vc, "_request_with_retry", side_effect=fake_request):
        result = vc.time_matrix(sources, targets)

    # Must NOT be one call — 6x7 is unsafe.
    assert len(calls) > 1, "6x7 should be chunked, not one call"
    assert_no_unsafe_shapes(calls)
    assert_matrix_correct(result, 6, 7)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

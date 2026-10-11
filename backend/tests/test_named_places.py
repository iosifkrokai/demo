"""Tests for the named-place path: token extraction, geo-anchoring, and integration."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from planner.models import Candidate, LatLon
from planner.pipeline import _geo_focus, should_skip_geo_focus


def test_region_scope_may_keep_its_spread_without_a_position():
    """«Все костёлы области» without a GPS fix keeps the regional spread."""
    assert should_skip_geo_focus(region_scope=True, origin=None) is True


def test_region_scope_does_not_discard_the_tourists_position():
    """Measured before the rule: with an origin AND a 120-min budget, the same
    query returned two stops 177 km apart and 38 hours of walking."""
    origin = LatLon(lat=53.6778, lon=23.8295)
    assert should_skip_geo_focus(region_scope=True, origin=origin) is False


def test_a_local_query_always_runs_the_focus():
    assert should_skip_geo_focus(region_scope=False, origin=None) is False
    assert should_skip_geo_focus(region_scope=False, origin=LatLon(lat=53.6, lon=23.8)) is False


def _c(id: int, name: str, lat: float, lon: float, rrf_score: float = 0.0) -> Candidate:
    return Candidate(
        id=id,
        name=name,
        category="замок",
        lat=lat,
        lon=lon,
        rrf_score=rrf_score,
    )


class TestGeoFocus:
    """Verify the anchor priority and the fixed 3x radius."""

    def test_origin_wins_over_anchor_id(self):
        """Origin GPS is the highest-priority anchor."""
        c1 = _c(1, "A", lat=53.9, lon=26.5, rrf_score=1.0)
        c2 = _c(2, "B", lat=54.0, lon=27.0, rrf_score=0.9)
        origin = LatLon(lat=53.9, lon=26.5)
        result = _geo_focus([c1, c2], origin=origin, anchor_id=2)
        assert result[0].id == 1

    def test_named_anchor_wins_over_top_rrf(self):
        """When no origin, named anchor (anchor_id) wins over top-RRF."""
        c_anchor = _c(5, "Anchor", lat=53.95, lon=26.55, rrf_score=0.5)
        c_top = _c(6, "TopRRF", lat=53.90, lon=26.50, rrf_score=1.0)
        result = _geo_focus([c_anchor, c_top], anchor_id=5)
        assert result[0].id == 5

    def test_top_rrf_anchors_without_origin_or_anchor(self):
        """Without origin or anchor_id, highest RRF score is used as anchor."""
        c_low = _c(3, "Low", lat=53.9, lon=26.5, rrf_score=0.1)
        c_high = _c(4, "High", lat=53.8, lon=26.6, rrf_score=0.9)
        result = _geo_focus([c_low, c_high])
        assert len(result) == 2
        ids = {c.id for c in result}
        assert ids == {3, 4}

    def test_named_anchor_fixed_3x_radius(self):
        """Named anchor returns candidates within 3x GEO_FOCUS_KM, not padded."""
        anchor = _c(38, "Мирский замок", lat=53.9506, lon=26.4675, rrf_score=1.0)
        nearby = _c(39, "Костёл Николая (Мир)", lat=53.948, lon=26.470, rrf_score=0.8)
        far_away = _c(49, "Старый замок Гродно", lat=53.6693, lon=23.8301, rrf_score=0.6)

        result = _geo_focus([anchor, nearby, far_away], anchor_id=38)
        ids = {c.id for c in result}
        assert 38 in ids
        assert 39 in ids
        assert 49 not in ids

    def test_named_anchor_isolated_keeps_radius(self):
        """An isolated named place returns just the anchor — the radius is never
        inflated to pad the pool; the pipeline reports the miss instead."""
        anchor = _c(1, "Anchor", lat=53.95, lon=26.47, rrf_score=1.0)
        far = _c(2, "Far", lat=54.0, lon=27.0, rrf_score=0.9)
        result = _geo_focus([anchor, far], anchor_id=1)
        assert [c.id for c in result] == [1], (
            "isolated anchor: only the anchor survives the fix radius"
        )

    def test_named_anchor_within_radius_exactly_2(self):
        """Exactly 2 candidates within radius: 3x branch returns them both."""
        anchor = _c(1, "Anchor", lat=53.95, lon=26.47, rrf_score=1.0)
        near1 = _c(2, "Near1", lat=53.96, lon=26.48, rrf_score=0.9)
        far = _c(3, "Far", lat=54.1, lon=27.0, rrf_score=0.8)

        result = _geo_focus([anchor, near1, far], anchor_id=1)
        ids = {c.id for c in result}
        assert ids == {1, 2}

    def test_no_anchor_doubles_radius_until_3(self):
        """Without a named anchor the radius doubles until ≥ 3 candidates found."""
        anchor_c = _c(1, "Anchor", lat=53.95, lon=26.47, rrf_score=1.0)
        c12 = _c(2, "At7km", lat=53.95, lon=26.58, rrf_score=0.9)
        c20 = _c(3, "At18km", lat=53.95, lon=26.75, rrf_score=0.8)

        result = _geo_focus([anchor_c, c12, c20])
        ids = {c.id for c in result}
        assert ids == {1, 2, 3}

    def test_empty_candidates_returns_empty(self):
        assert _geo_focus([]) == []
        assert _geo_focus([], anchor_id=1) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

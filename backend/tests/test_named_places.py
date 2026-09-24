"""Tests for the named-place path: token extraction, geo-anchoring, and integration.

Covers:
  - _extract_named_place_tokens: regex + stop-list
  - extract_intent (named_places field, mocked LLM)
  - _geo_focus: anchor priority, fixed 3x radius for named anchor
"""

from __future__ import annotations

import os
import re as _re
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.models import Candidate, LatLon
from agent.planner.intent import _PLACE_STOP_LIST
from agent.planner.pipeline import _geo_focus

# ── Replicated token-extraction logic (mirrors intent.py exactly) ───────────

def _extract_named_place_tokens(query: str) -> list[str]:

    tokens = _re.findall(r"[А-ЯЁ][а-яё\-]{2,}", query)
    return [t for t in tokens if t.lower() not in _PLACE_STOP_LIST]


# ── Shared mock for jev.ask ─────────────────────────────────────────────────
# jev.noul reads answer["noul"]; jev.choice reads answer["choice"];
# jev.score reads answer["score"].  All answers include "confidence".

def _mock_jev_ask(query: str, questions: dict) -> dict:
    all_cats = [
        "замок", "костёл", "церковь", "монастырь", "дворец", "усадьба",
        "парк", "музей", "памятник", "храм", "архитектура",
        "инфраструктура", "кладбище",
    ]
    return {
        **{f"cat_{cat}": {"noul": 0.0, "confidence": 0.9} for cat in all_cats},
        **{f"neg_{cat}": {"noul": 0.0, "confidence": 0.9} for cat in all_cats},
        "intent_type": {"choice": "specific", "confidence": 0.9},
        "party_type": {"choice": "solo", "confidence": 0.9},
        "era_hint": {"choice": "any", "confidence": 0.9},
        "search_scope": {"choice": "town", "confidence": 0.9},
        "mentions_named_place": {"noul": 1.0, "confidence": 0.9},
        "time_hours": {"score": 2, "confidence": 0.9},
    }


# ── Fake Candidate builder ──────────────────────────────────────────────────

def _c(id: int, name: str, lat: float, lon: float, rrf_score: float = 0.0) -> Candidate:
    return Candidate(
        id=id,
        name=name,
        category="замок",
        lat=lat,
        lon=lon,
        rrf_score=rrf_score,
    )


# ── Token-extraction tests ───────────────────────────────────────────────────

class TestExtractNamedPlaceTokens:
    """Defect 1: stop-list was comparing t.lower() against CAPITALISED entries,
    so {"Гродненская", "Область"} never matched anything.  Fixed by:
      a) lowercasing the stop-list entries, and
      b) covering nominative + inflected forms (genitive "Гродненской", etc.)
    These tests verify both the fix and the correct regex behaviour."""

    def test_single_named_place(self):
        # "Мирскому" is the inflected form — regex captures it.
        tokens = _extract_named_place_tokens("Хочу к Мирскому замку")
        assert "Мирскому" in tokens
        assert "Хочу" in tokens  # sentence-initial verb (harmless; resolves to 0 rows)

    def test_multi_place_captures_proper_nouns(self):
        # "Новогрудок" (capital N) — captured; "замковая" starts lowercase.
        tokens = _extract_named_place_tokens("Новогрудок, замковая гора и центр")
        assert "Новогрудок" in tokens
        assert "центр" not in tokens  # lowercase

    def test_sentence_initial_verb_captured(self):
        # "Хочу" starts with capital letter (sentence start) but is not a place.
        tokens = _extract_named_place_tokens("Хочу погулять по замкам Гродно")
        assert "Хочу" in tokens    # captured by regex (sentence-initial verb)
        assert "Гродно" in tokens  # real toponym
        # "замкам" starts lowercase so not captured (correct)

    def test_stop_list_nominative(self):
        # "Гродненская" and "область" must be dropped even when capitalised.
        assert _extract_named_place_tokens("Гродненская область") == []

    def test_stop_list_inflected_genitive(self):
        # Genitive: "Гродненской области", "Гродненской" etc.
        assert _extract_named_place_tokens("достопримечательности Гродненской области") == []

    def test_stop_list_filters_region_preserves_real_place(self):
        tokens = _extract_named_place_tokens("музеи Гродненской области и Новогрудок")
        lowered = {t.lower() for t in tokens}
        assert "гродненской" not in lowered
        assert "области" not in lowered
        assert "Новогрудок" in tokens   # real place survives


# ── _geo_focus tests ─────────────────────────────────────────────────────────

class TestGeoFocus:
    """Defect 4: anchor_id branch had dead assignment `anchor3 = anchor` and
    could return ALL candidates when len(keep) < 2 (acceptable fallback).
    These tests verify the anchor priority and the fixed 3x radius."""

    def test_origin_wins_over_anchor_id(self):
        """Origin GPS is the highest-priority anchor."""
        # Use valid Belarus coordinates (LatLon bounds: lat 44-62, lon 19-42)
        c1 = _c(1, "A", lat=53.9, lon=26.5, rrf_score=1.0)
        c2 = _c(2, "B", lat=54.0, lon=27.0, rrf_score=0.9)
        origin = LatLon(lat=53.9, lon=26.5)   # same as c1
        result = _geo_focus([c1, c2], origin=origin, anchor_id=2)
        # Origin is the anchor; distance check keeps c1 (anchor itself).
        assert result[0].id == 1

    def test_named_anchor_wins_over_top_rrf(self):
        """When no origin, named anchor (anchor_id) wins over top-RRF."""
        c_anchor = _c(5, "Anchor", lat=53.95, lon=26.55, rrf_score=0.5)
        c_top = _c(6, "TopRRF", lat=53.90, lon=26.50, rrf_score=1.0)
        result = _geo_focus([c_anchor, c_top], anchor_id=5)
        # Named anchor wins; result is ordered by proximity to anchor.
        assert result[0].id == 5   # anchor itself (distance 0)

    def test_top_rrf_anchors_without_origin_or_anchor(self):
        """Without origin or anchor_id, highest RRF score is used as anchor."""
        c_low = _c(3, "Low", lat=53.9, lon=26.5, rrf_score=0.1)
        c_high = _c(4, "High", lat=53.8, lon=26.6, rrf_score=0.9)
        result = _geo_focus([c_low, c_high])
        # Both within 12 km → both kept.
        assert len(result) == 2
        ids = {c.id for c in result}
        assert ids == {3, 4}

    def test_named_anchor_fixed_3x_radius(self):
        """Named anchor returns candidates within 3x GEO_FOCUS_KM, not padded.
        GEO_FOCUS_KM = 12 → 3x = 36 km.
        Mir castle (id=38): 53.9506 N, 26.4675 E
        Grodno (id=49): ~53.6693 N, 23.8301 E — ≈ 173 km >> 36 km, must be excluded."""
        anchor = _c(38, "Мирский замок", lat=53.9506, lon=26.4675, rrf_score=1.0)
        nearby = _c(39, "Костёл Николая (Мир)", lat=53.948, lon=26.470, rrf_score=0.8)
        # ~2 km away — within 36 km ✓
        far_away = _c(49, "Старый замок Гродно", lat=53.6693, lon=23.8301, rrf_score=0.6)
        # ≈ 173 km — outside 3x radius ✗

        result = _geo_focus([anchor, nearby, far_away], anchor_id=38)
        ids = {c.id for c in result}
        assert 38 in ids      # anchor itself always included
        assert 39 in ids      # nearby — within 36 km
        assert 49 not in ids  # Groдно — > 36 km, must NOT be pulled in

    def test_named_anchor_isolated_keeps_radius(self):
        """An isolated named place returns just the anchor — we never inflate the
        radius to pad the pool.  Pulling a 45 km-away candidate in used to build
        a route whose only leg was unwalkable, and the optimizer then dropped
        everything but one stop (422).  The pipeline reports the miss instead."""
        anchor = _c(1, "Anchor", lat=53.95, lon=26.47, rrf_score=1.0)
        far = _c(2, "Far", lat=54.0, lon=27.0, rrf_score=0.9)
        result = _geo_focus([anchor, far], anchor_id=1)
        assert [c.id for c in result] == [1], \
            "isolated anchor: only the anchor survives the fix radius"

    def test_named_anchor_within_radius_exactly_2(self):
        """Exactly 2 candidates within radius: 3x branch returns them both."""
        anchor = _c(1, "Anchor", lat=53.95, lon=26.47, rrf_score=1.0)
        near1 = _c(2, "Near1", lat=53.96, lon=26.48, rrf_score=0.9)
        far = _c(3, "Far", lat=54.1, lon=27.0, rrf_score=0.8)

        result = _geo_focus([anchor, near1, far], anchor_id=1)
        ids = {c.id for c in result}
        assert ids == {1, 2}   # only anchor and near1 (both within 3x radius)

    def test_no_anchor_doubles_radius_until_3(self):
        """Without a named anchor the radius doubles until ≥ 3 candidates found."""
        anchor_c = _c(1, "Anchor", lat=53.95, lon=26.47, rrf_score=1.0)
        c12 = _c(2, "At12km", lat=53.95, lon=26.58, rrf_score=0.9)   # ≈ 8.5 km
        c20 = _c(3, "At20km", lat=53.95, lon=26.65, rrf_score=0.8)   # ≈ 14 km

        # Only 1 within 12 km → radius doubles → c20 included
        result = _geo_focus([anchor_c, c12, c20])
        ids = {c.id for c in result}
        assert ids == {1, 2, 3}   # doubling includes c20

    def test_empty_candidates_returns_empty(self):
        assert _geo_focus([]) == []
        assert _geo_focus([], anchor_id=1) == []


# ── extract_intent integration (mock LLM) ────────────────────────────────────

class TestExtractIntentNamedPlaces:
    """Verify that extract_intent populates named_places correctly when the
    LLM part is mocked.  This exercises the full pipeline from query to
    IntentDecision.named_places without any OpenRouter call."""

    def test_named_places_single(self):
        # Patch jev.ask at the module where intent.py imports it from.
        with patch("agent.jev.ask", side_effect=_mock_jev_ask):
            from agent.planner.intent import extract_intent
            result = extract_intent("Хочу к Мирскому замку")
        named = result.decision.named_places
        assert "Мирскому" in named
        # "Хочу" is captured (sentence-initial verb) — harmless; resolves to 0 rows.

    def test_named_places_region_name_filtered(self):
        with patch("agent.jev.ask", side_effect=_mock_jev_ask):
            from agent.planner import intent as intent_mod
            result = intent_mod.extract_intent(
                "достопримечательности Гродненской области"
            )
        named = result.decision.named_places
        # The region name should be filtered out (defect-1 fix)
        lowered = {t.lower() for t in named}
        assert "гродненской" not in lowered
        assert "области" not in lowered

    def test_named_places_mixed_verb_and_real_place(self):
        with patch("agent.jev.ask", side_effect=_mock_jev_ask):
            from agent.planner import intent as intent_mod
            result = intent_mod.extract_intent("Хочу погулять по замкам Гродно")
        named = result.decision.named_places
        assert "Гродно" in named
        assert "Хочу" in named   # sentence-initial verb — harmless downstream


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

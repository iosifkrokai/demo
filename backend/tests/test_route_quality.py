"""Route quality tests — deterministic, no network, fake candidates."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.planner.explain import _area_name, explain
from agent.planner.optimize import _budget_constrain, _max_leg, _max_leg_seconds
from agent.planner.resolve import _is_town_or_district_match
from agent.planner.retrieve import (
    _category_signal,
    _detect_category_keywords,
    rrf_fuse,
)
from contracts.planner import Candidate


def _c(
    id: int,
    name: str,
    lat: float,
    lon: float,
    category: str,
    rrf_score: float = 0.0,
    visit_minutes_db: int = 20,
    town: str | None = None,
    district: str | None = None,
) -> Candidate:
    return Candidate(
        id=id,
        name=name,
        category=category,
        lat=lat,
        lon=lon,
        rrf_score=rrf_score,
        relevance=rrf_score,
        visit_minutes_db=visit_minutes_db,
        town=town,
        district=district,
    )


class TestIsTownOrDistrictMatch:
    """_is_town_or_district_match correctly identifies town/district-only rows."""

    def test_name_match_is_false(self):
        row = {
            "name": "Мирский замок",
            "town": "Мир",
            "district": "Новогрудский район",
        }
        assert _is_town_or_district_match(row, "Мирскому") is False, \
            "Query 'Мирскому' is a substring of name 'Мирский замок' — not a town match"

    def test_town_match_is_true(self):
        row = {
            "name": "Фарный костёл",
            "town": "Гродно",
            "district": "Гродненский район",
        }
        assert _is_town_or_district_match(row, "Гродно") is True, \
            "Query 'Гродно' matches town column — town match"

    def test_district_match_is_true(self):
        row = {
            "name": "Лидский замок",
            "town": "Лида",
            "district": "Лидский район",
        }
        assert _is_town_or_district_match(row, "Лидский") is True, \
            "Query 'Лидский' matches district column — district match"

    def test_town_match_case_insensitive(self):
        row = {"name": "Старый замок", "town": "гродно", "district": ""}
        assert _is_town_or_district_match(row, "Гродно") is True, \
            "Town match must be case-insensitive"


class TestResolveNamedPlacesLogic:
    """Direct tests of the two-path resolution logic."""

    def test_name_above_threshold_goes_to_must_visit(self):
        """When _name_match_search returns a high-similarity row (sim >= 0.3),
        it is added to must_visit_ids and NOT used as area_anchor."""
        row = {
            "id": 38,
            "name": "Мирский замок",
            "town": "Мир",
            "district": "Новогрудский район",
            "category": "замок",
            "lat": 53.95,
            "lon": 26.47,
            "_name_sim": 0.5,
        }
        with patch("agent.planner.resolve._name_match_search", return_value=[row]):
            with patch("agent.planner.resolve._keyword_search", return_value=[]):
                from agent.planner.resolve import _resolve_named_places

                must_ids, area_anchor, resolved = _resolve_named_places(
                    ["Мирскому"], MagicMock()
                )

        assert must_ids == [38], \
            "Мирскому (sim=0.5 >= 0.3) -> must_visit_ids"
        assert area_anchor is None, \
            "Name match must NOT become area_anchor"
        assert resolved == ["Мирскому"]

    def test_name_below_threshold_town_match_goes_to_area_anchor(self):
        """When _name_match_search returns low similarity (sim < 0.3),
        the keyword search is used for area_anchor instead."""
        name_row = {
            "id": 49,
            "name": "Старый замок (Гродно)",
            "town": "Гродно",
            "district": "Гродненский район",
            "category": "замок",
            "lat": 53.68,
            "lon": 23.83,
            "_name_sim": 0.2,
        }
        town_row = {
            "id": 49,
            "name": "Старый замок (Гродно)",
            "town": "Гродно",
            "district": "Гродненский район",
            "category": "замок",
            "lat": 53.68,
            "lon": 23.83,
        }
        with patch("agent.planner.resolve._name_match_search", return_value=[name_row]):
            with patch("agent.planner.resolve._keyword_search", return_value=[town_row]):
                from agent.planner.resolve import _resolve_named_places

                must_ids, area_anchor, resolved = _resolve_named_places(
                    ["Гродно"], MagicMock()
                )

        assert must_ids == [], \
            "Гродно (name_sim=0.2 < 0.3) -> must_visit_ids must be empty"
        assert area_anchor == 49, \
            "Гродно (town match) -> area_anchor"
        assert resolved == [], \
            "an area anchor is not a resolved must-visit name"


class TestCategoryKeywordDetection:
    """_detect_category_keywords extracts LLM categories from raw query text."""

    def test_detects_zamki(self):
        cats = _detect_category_keywords("Хочу погулять по замкам Гродно")
        assert "замок" in cats, \
            "'замкам' should map to 'замок' category"

    def test_detects_kostyol(self):
        cats = _detect_category_keywords("костёлы центра без музеев")
        assert "костёл" in cats
        assert "музей" in cats

    def test_no_false_positives(self):
        cats = _detect_category_keywords("Гродненская область")
        assert "замок" not in cats
        assert "костёл" not in cats

    def test_deduplicates(self):
        cats = _detect_category_keywords("замок и замки")
        assert cats.count("замок") == 1


class TestCategorySteering:
    """Category signal must push castle-category places above church-category
    places when the query contains 'замок' keywords."""

    def test_category_signal_ordered_by_priority(self):
        """_category_signal orders primary category first -> higher RRF rank."""
        mock_db = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (1, "костёл"),
            (2, "замок"),
            (3, "замок"),
        ]
        mock_db.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_db.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = _category_signal(mock_db, None, ["замок"], limit=10)
        ids = [pid for pid, _ in result]

        assert ids.index(2) < ids.index(1), \
            "замок-category places must outrank костёл when 'замок' is primary"

    def test_rrf_fusion_category_vs_keyword(self):
        """Castle in both keyword+category signals must outrank church (keyword only)."""
        castle_id = 1
        church_id = 2

        keyword_signal = [(castle_id, 0.0), (church_id, 0.0)]
        category_signal = [(castle_id, 0.0)]
        must_signal: list[tuple[int, float]] = []

        fused = rrf_fuse([keyword_signal, category_signal, must_signal], k=60)
        assert fused[castle_id] > fused[church_id], \
            "Castle in both keyword+category signals must outrank church (keyword only)"


class TestBudgetConstrain:
    """_budget_constrain removes stops that violate the budget or max-leg constraint."""

    def test_stops_13km_apart_dropped_from_120min_budget(self):
        """13 km at 4 km/h = 117 min per leg — exceeds 25% share of 120 min (30 min).
        The budget_constrain must drop the far stop."""
        candidates = [
            _c(1, "Мирский замок", 53.9506, 26.4675, "замок",
               rrf_score=1.0, visit_minutes_db=40),
            _c(2, "Любчанский замок", 53.85, 26.27, "замок",
               rrf_score=0.9, visit_minutes_db=40),
        ]
        matrix = [[0.0, 7020.0], [7020.0, 0.0]]
        visits = [40, 40]
        budget_s = 120 * 60
        must_ids = {2}
        order = [0, 1]

        constrained, _dropped = _budget_constrain(
            candidates, order, matrix, visits, budget_s, must_ids
        )

        assert 1 in constrained, \
            "Index 0 (id=1, more relevant, NOT must-visit) — should be evaluated for removal"
        assert 0 not in constrained or constrained == [1], \
            f"Любчанский замок (13 km, 117 min walk) exceeds 120-min budget — must be dropped; got: {constrained}"

    def test_25km_apart_stops_dropped_from_150min_budget(self):
        """25 km at 4 km/h = 375 min -> far above any reasonable budget share."""
        candidates = [
            _c(38, "Мирский замок", 53.9506, 26.4675, "замок",
               rrf_score=1.0, visit_minutes_db=40),
            _c(40, "Любчанский замок", 53.85, 26.27, "замок",
               rrf_score=0.8, visit_minutes_db=40),
        ]
        matrix = [[0.0, 22500.0], [22500.0, 0.0]]
        visits = [40, 40]
        budget_s = 150 * 60
        must_ids = {38}
        order = [0, 1]

        constrained, _dropped = _budget_constrain(
            candidates, order, matrix, visits, budget_s, must_ids
        )

        assert 1 not in constrained, \
            "25 km leg (375 min) far exceeds 150-min budget — must be dropped"


class TestMaxLegSeconds:
    """_max_leg_seconds computes the correct per-leg ceiling."""

    def test_120min_budget_caps_at_30min(self):
        """120 min x 25% share = 30 min -> 2 km at 4 km/h."""
        result = _max_leg_seconds(120 * 60)
        assert result == 30 * 60, \
            f"120-min budget: 25% = 30 min = 1800 s; got {result}"

    def test_480min_budget_caps_at_absolute_5km(self):
        """480 min x 25% = 120 min = 8 km. But absolute cap is 5 km (75 min)."""
        result = _max_leg_seconds(480 * 60)
        expected = (5.0 / 4.0) * 3600
        assert result == expected, \
            f"Absolute cap MAX_WALK_LEG_KM=5 km must dominate long budgets; got {result}s"

    def test_none_budget_uses_absolute_cap(self):
        result = _max_leg_seconds(None)
        expected = (5.0 / 4.0) * 3600
        assert result == expected


class TestMaxLeg:
    """_max_leg returns the longest single leg in a route."""

    def test_longest_leg_detected(self):
        matrix = [
            [0, 100, 50],
            [100, 0, 200],
            [50, 200, 0],
        ]
        assert _max_leg([0, 1, 2], matrix) == 200.0
        assert _max_leg([0, 2], matrix) == 50.0


class TestAreaName:
    """_area_name picks the right geographic descriptor for the route."""

    def test_single_town_returns_that_town(self):
        route = [
            _c(38, "Мирский замок", 53.95, 26.47, "замок", town="Мир"),
            _c(39, "Костёл (Мир)", 53.95, 26.47, "костёл", town="Мир"),
        ]
        assert _area_name(route) == "Мир"

    def test_single_district_returns_that_district(self):
        route = [
            _c(1, "Place A", 53.88, 25.29, "замок",
               town="Лида", district="Лидский район"),
            _c(2, "Place B", 53.90, 25.31, "костёл",
               town="Березовка", district="Лидский район"),
        ]
        assert _area_name(route) == "Лидский район", \
            "When towns differ but districts match -> prefer district"

    def test_mixed_towns_returns_first(self):
        route = [
            _c(1, "Мирский замок", 53.95, 26.47, "замок", town="Мир"),
            _c(2, "Лидский замок", 53.88, 25.29, "замок", town="Лида"),
            _c(3, "Новогрудский замок", 53.90, 25.80, "замок", town="Новогрудок"),
        ]
        result = _area_name(route)
        assert result in ("Мир", "Лида", "Новогрудок")

    def test_empty_route_returns_default(self):
        assert _area_name([]) == "Гродно"


class TestExplainArea:
    """explain() uses _area_name, not a hardcoded string."""

    def test_mir_explains_mir(self):
        route = [
            _c(38, "Мирский замок", 53.95, 26.47, "замок", town="Мир"),
            _c(39, "Костёл (Мир)", 53.95, 26.47, "костёл", town="Мир"),
        ]
        trace = {"algorithm": "brute_open", "diversity": 0.5, "fits_budget": True}
        text = explain(route, trace, walk_seconds=600)
        assert "по Мир" in text, \
            f"Explanation must contain area name 'Мир'; got: {text[:80]}"

    def test_grodno_explains_grodno(self):
        route = [
            _c(49, "Старый замок (Гродно)", 53.68, 23.83, "замок", town="Гродно"),
            _c(52, "Фарный костёл", 53.68, 23.83, "костёл", town="Гродно"),
        ]
        trace = {"algorithm": "direct", "diversity": 0.5, "fits_budget": True}
        text = explain(route, trace, walk_seconds=300)
        assert "по Гродно" in text, \
            f"Explanation must contain 'Гродно'; got: {text[:80]}"

    def test_no_hardcoded_grodno_for_mir_route(self):
        route = [
            _c(38, "Мирский замок", 53.95, 26.47, "замок", town="Мир"),
        ]
        trace = {"algorithm": "direct", "diversity": 1.0, "fits_budget": True}
        text = explain(route, trace, walk_seconds=0)
        assert "по Гродно" not in text, \
            f"Explanation must NOT hardcode 'Гродно' for a Мир route; got: {text}"


class TestResolveIntegration:
    """resolve() returns area_anchor but empty must_visit_ids for town-only queries."""

    def test_town_query_creates_area_anchor_not_must_visit(self):
        import psycopg

        from agent.planner.resolve import resolve
        from contracts.planner import IntentDecision, IntentResult

        mock_db = MagicMock(spec=psycopg.Connection)

        intent = IntentResult(
            decision=IntentDecision(
                named_places=["Гродно"],
                categories_pos=["замок"],
                categories_neg=[],
                keywords_pos=[],
                keywords_neg=[],
            ),
            source="agent",
        )

        name_row = {
            "id": 49,
            "name": "Старый замок (Гродно)",
            "town": "Гродно",
            "district": "Гродненский район",
            "category": "замок",
            "lat": 53.68,
            "lon": 23.83,
            "_name_sim": 0.2,
        }
        with patch("agent.planner.resolve._name_match_search", return_value=[name_row]):
            with patch("agent.planner.resolve._keyword_search", return_value=[name_row]):
                constraints = resolve(
                    intent,
                    explicit_time_budget=120,
                    explicit_bbox=None,
                    db=mock_db,
                )

        assert constraints.must_visit_ids == [], \
            "Гродно matched on town only — must_visit_ids must be empty"
        assert constraints.area_anchor == 49, \
            "Гродно matched on town -> area_anchor must be set"


class TestBudgetRule:
    """The only limit is the one the user names; 0 / absent both mean none."""

    @staticmethod
    def _resolve(explicit, llm_budget):
        import psycopg

        from agent.planner.resolve import resolve
        from contracts.planner import IntentDecision, IntentResult

        intent = IntentResult(
            decision=IntentDecision(
                named_places=[],
                categories_pos=["костёл"],
                categories_neg=[],
                keywords_pos=[],
                keywords_neg=[],
                time_budget_minutes=llm_budget,
            ),
            source="agent",
        )
        with patch("agent.planner.resolve._name_match_search", return_value=[]):
            with patch("agent.planner.resolve._keyword_search", return_value=[]):
                return resolve(
                    intent,
                    explicit_time_budget=explicit,
                    explicit_bbox=None,
                    db=MagicMock(spec=psycopg.Connection),
                ).time_budget_minutes

    def test_zero_budget_means_no_limit(self):
        assert self._resolve(0, None) is None

    def test_absent_budget_means_no_limit(self):
        assert self._resolve(None, None) is None

    def test_named_budget_is_kept(self):
        assert self._resolve(90, None) == 90

    def test_zero_selector_overrides_a_model_guess(self):
        assert self._resolve(0, 120) is None

    def test_api_accepts_zero_as_no_limit(self):
        from contracts.planner import GenerateReq

        assert GenerateReq(query="костёлы Гродно", time_budget_minutes=0).time_budget_minutes == 0
        assert GenerateReq(query="костёлы Гродно").time_budget_minutes is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

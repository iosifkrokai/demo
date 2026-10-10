"""Canonical taxonomy + category→DB mapping."""

from __future__ import annotations

import pytest

from core import constants
from reference.taxonomy import (
    all_categories,
    all_codes,
    db_values,
    get,
    resolve_code,
    role,
    visit_minutes,
)


class TestTaxonomyData:

    def test_taxonomy_is_not_empty(self):
        assert len(all_categories()) >= 15
        assert len(all_codes()) == len(all_categories())

    def test_every_category_has_a_well_formed_osm_mapping(self):
        """No code may exist without at least one real OSM key=value tag."""
        for cat in all_categories():
            assert cat.osm_tags, f"{cat.code}: no OSM tag mapping"
            for tag in cat.osm_tags:
                key, _, value = tag.partition("=")
                assert key and value, f"{cat.code}: malformed OSM tag {tag!r}"

    def test_every_category_has_positive_visit_minutes(self):
        for cat in all_categories():
            assert cat.visit_minutes > 0, f"{cat.code}: visit_minutes must be > 0"

    def test_every_category_has_names_and_valid_role(self):
        for cat in all_categories():
            assert cat.code and cat.ru and cat.en
            assert cat.role in ("sight", "service")
            assert cat.code == cat.ru

    def test_codes_are_unique(self):
        codes = all_codes()
        assert len(codes) == len(set(codes))

    def test_indispensable_services_are_present(self):
        for code in ("кафе", "ресторан", "туалет", "гостиница"):
            assert code in all_codes(), code
            assert role(code) == "service"


class TestRoundTrip:

    def test_every_code_maps_to_itself_as_a_db_value(self):
        """taxonomy → db_values must round-trip for EVERY code, so each code
        can reach the `category = ANY(%s)` filter."""
        for code in all_codes():
            assert db_values([code]) == [code], code

    def test_all_codes_round_trip_together_in_order(self):
        assert db_values(all_codes()) == list(all_codes())

    def test_db_values_never_returns_unknown_codes(self):
        assert db_values(["неизвестно", "замок"]) == ["замок"]
        assert db_values(["замок", "castle"]) == ["замок"]


class TestServicesNoLongerEmpty:
    """The reported defect: these four returned [] from db_values()."""

    @pytest.mark.parametrize("code", ["кафе", "ресторан", "туалет", "гостиница"])
    def test_service_category_maps_to_itself(self, code):
        assert db_values([code]) == [code]
        assert db_values([code]) == [code]

    def test_the_code_mapping_is_not_empty(self):
        assert db_values(["туалет"]) == ["туалет"]
        assert db_values(["кафе", "ресторан", "туалет", "гостиница"]) == [
            "кафе", "ресторан", "туалет", "гостиница",
        ]

    def test_convenience_categories_all_map(self):
        assert constants.CONVENIENCE_CATEGORIES == (
            "кафе",
            "ресторан",
            "туалет",
            "гостиница",
            "остановка транспорта",
        )
        for code in constants.CONVENIENCE_CATEGORIES:
            assert db_values([code]) == [code]


class TestResolveCode:

    @pytest.mark.parametrize(
        "term,expected",
        [
            ("туалет", "туалет"),
            ("замок", "замок"),
            ("coffee", "кафе"),
            ("туалеты", "туалет"),
            ("замков", "замок"),
            ("костёлы", "костёл"),
            ("музеями", "музей"),
            ("cafe", "кафе"),
            ("restaurant", "ресторан"),
            ("restaurants", "ресторан"),
            ("toilet", "туалет"),
            ("hotel", "гостиница"),
            ("КОСТЕЛ", "костёл"),
            ("Кофейня", "кафе"),
        ],
    )
    def test_resolves_expected_code(self, term, expected):
        assert resolve_code(term) == expected

    @pytest.mark.parametrize("term", ["", "   ", "вертолёт", "zzzz", None])
    def test_unknown_terms_return_none(self, term):
        assert resolve_code(term) is None

    @pytest.mark.parametrize(
        "term,expected",
        [
            ("автобус", "остановка транспорта"),
            ("троллейбус", "остановка транспорта"),
            ("маршрутка", "остановка транспорта"),
            ("автобусная остановка", "остановка транспорта"),
            ("остановка", None),
            ("транспорт", None),
        ],
    )
    def test_transit_words_resolve_but_the_bare_word_does_not(self, term, expected):
        assert resolve_code(term) == expected

    def test_every_code_resolves_to_itself(self):
        for code in all_codes():
            assert resolve_code(code) == code, code

    def test_resolved_code_is_always_canonical(self):
        for term in ("туалеты", "cafe", "coffee", "замков", "hotels"):
            code = resolve_code(term)
            assert code in all_codes()


class TestDbValues:

    def test_deduplicates_and_keeps_order(self):
        assert db_values(["кафе", "туалет", "кафе"]) == ["кафе", "туалет"]

    def test_drops_unknown_terms(self):
        assert db_values(["неизвестно", "замок"]) == ["замок"]
        assert db_values(["неизвестно"]) == []

    def test_accepts_inflected_terms(self):
        assert db_values(["туалеты"]) == ["туалет"]
        assert db_values(["cafes"]) == ["кафе"]

    def test_empty_input(self):
        assert db_values([]) == []


class TestAccessorsAndConstants:

    def test_get(self):
        assert get("кафе") is not None
        assert get("кафе").code == "кафе"
        assert get("no-such-code") is None

    def test_role_and_visit_minutes(self):
        assert role("замок") == "sight"
        assert role("туалет") == "service"
        assert visit_minutes("замок") == 40
        assert visit_minutes("туалет") == 10

    def test_role_and_visit_minutes_raise_on_unknown(self):
        with pytest.raises(KeyError):
            role("no-such-code")
        with pytest.raises(KeyError):
            visit_minutes("no-such-code")

    def test_constants_categories_come_from_the_taxonomy(self):
        assert all_codes() == constants.CATEGORIES

    def test_constants_convenience_categories_are_the_service_role(self):
        service_codes = tuple(c.code for c in all_categories() if c.role == "service")
        assert service_codes == constants.CONVENIENCE_CATEGORIES

    def test_constants_visit_times_come_from_the_taxonomy(self):
        expected = {c.code: c.visit_minutes for c in all_categories()}
        assert expected == constants.VISIT_TIME_BY_CATEGORY


class TestIntentCategoriesCantDriftFromTheTaxonomy:
    """The vocabulary the planner accepts is the taxonomy's own, not a copy."""

    def test_intent_decision_accepts_every_taxonomy_code(self):
        from pydantic import ValidationError

        from planner.models import IntentDecision

        for code in all_codes():
            assert IntentDecision(categories_pos=[code]).categories_pos == [code], code

        with pytest.raises(ValidationError):
            IntentDecision(categories_pos=["нет такой категории"])

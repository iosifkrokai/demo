"""The no-LLM intent fallback: deterministic, map-driven, never a failure.

This file pins the degraded contract for step 1 (intent) — the layer that
used to answer «Хочу погулять по замкам Гродно» with a canteen and a toilet
because the fallback extracted NO category at all.

What is pinned here:
  * categories_pos is filled from the SAME deterministic keyword→category map
    retrieval uses (resolve.CATEGORY_SYNONYMS, inverted) — «замкам» → замок,
    «костёлам» → костёл — across singular/plural and case forms;
  * the no-LLM path never raises and answers a themed query with a non-empty
    category set; a category-less query gets an honest empty set;
  * time_budget_minutes survives only when the query states a duration
    («за 3 часа», «2 часа») — the existing deterministic helper;
  * named-place extraction is unchanged (DB-driven, no LLM);
  * with a stubbed LLM present the fallback is NOT used: the Jev path
    answers, byte for byte, as before.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import constants, jev
from agent.config import settings
from agent.planner import intent as intent_mod
from agent.planner.intent import _KEYWORD_TO_CATEGORY, extract_intent, fallback_intent
from agent.planner.resolve import CATEGORY_SYNONYMS

QUERY = "Хочу погулять по замкам Гродно"


# ── Fixtures: the key is the switch between degraded and full mode ───────────

@pytest.fixture
def no_key(monkeypatch):
    """A process without an OpenRouter key: the env var AND the import-time
    settings snapshot are cleared — otherwise a key from the developer's
    shell leaks in and the test would exercise the wrong branch."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None, raising=False)
    assert jev.available() is False


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-test-key", raising=False)
    assert jev.available() is True


@contextmanager
def offline() -> Iterator[None]:
    """Belt and braces: nothing in this block may reach OpenRouter."""
    old = jev.api_key
    jev.api_key = lambda: None  # type: ignore[assignment]
    try:
        yield
    finally:
        jev.api_key = old  # type: ignore[assignment]


def _explode(*_a, **_kw):
    raise AssertionError("the fallback must not be used when a stubbed LLM answers")


def _mock_jev_ask(_query: str, _questions: dict) -> dict:
    """The typed-answer shape a real /systemone call returns, with one category
    scored high — the fallback must ignore this map and use these answers."""
    return {
        **{f"cat_{cat}": {"noul": 0.0, "confidence": 0.9} for cat in constants.CATEGORIES},
        **{f"neg_{cat}": {"noul": 0.0, "confidence": 0.9} for cat in constants.CATEGORIES},
        "intent_type": {"choice": "themed", "confidence": 0.9},
        "party_type": {"choice": "solo", "confidence": 0.9},
        "era_hint": {"choice": "any", "confidence": 0.9},
        "search_scope": {"choice": "town", "confidence": 0.9},
        "mentions_named_place": {"noul": 1.0, "confidence": 0.9},
        "time_hours": {"score": 0, "confidence": 0.9},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Categories: the shared map, read off the query text
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackCategories:

    def test_the_live_query_extracts_its_category(self, no_key, monkeypatch):
        """The exact degraded request that answered with a canteen and a toilet:
        «замкам» → замок, so retrieval steers at castles."""
        monkeypatch.setattr(jev, "ask", _explode)
        d = extract_intent(QUERY).decision
        assert d.categories_pos == ["замок"]

    @pytest.mark.parametrize(
        "query,expected",
        [
            # замок — the live form plus its declension and synonyms
            ("погулять по замкам Гродно", "замок"),
            ("замки Гродно", "замок"),
            ("старый замок", "замок"),
            ("руины замка", "замок"),
            ("замку не хватает ухода", "замок"),
            ("крепость Гродно", "замок"),
            ("крепости в области", "замок"),
            # костёл — incl. the no-ё spelling queries/OSM names use
            ("костёлы Гродно", "костёл"),
            ("костёлам Новогрудка", "костёл"),
            ("костел без подъезда", "костёл"),
            # храм and its synonyms
            ("храмы города", "храм"),
            ("храма на горе", "храм"),
            ("кирхи Гродно", "храм"),
            ("синагогу посмотреть", "храм"),
            ("каплицы старого города", "храм"),
            # монастырь
            ("монастыри области", "монастырь"),
            ("монастыря не видно", "монастырь"),
            # музей
            ("музеи Гродно", "музей"),
            ("музея хватит на час", "музей"),
            # everyday stops
            ("где кафе рядом", "кафе"),
            ("кофейня в центре", "кафе"),
            ("кофейне не открыться", "кафе"),
            ("туалет на маршруте", "туалет"),
            ("уборная рядом", "туалет"),
            ("гостиница у вокзала", "гостиница"),
            ("хостела дешевле", "гостиница"),
            ("отеля с видом", "гостиница"),
            # the rest of the heritage taxonomy
            ("дворцы Лиды", "дворец"),
            ("усадьбы Несвижа", "усадьба"),
            ("парки и скверы Гродно", "парк"),
            ("памятники города", "памятник"),
            ("архитектура модерна", "архитектура"),
            ("мосты через Неман", "инфраструктура"),
            ("кладбище старое", "кладбище"),
            ("некрополь у костёла", "кладбище"),
            ("поесть в центре", "ресторан"),
            ("обед из трёх блюд", "ресторан"),
        ],
    )
    def test_categories_from_query_forms(self, no_key, monkeypatch, query, expected):
        """Singular/plural/case forms and synonyms all resolve through the map."""
        monkeypatch.setattr(jev, "ask", _explode)
        d = extract_intent(query).decision
        assert expected in d.categories_pos, query

    def test_multiple_categories_in_one_query(self, no_key, monkeypatch):
        monkeypatch.setattr(jev, "ask", _explode)
        d = extract_intent("костёлы и замки Новогрудка").decision
        assert {"костёл", "замок"} <= set(d.categories_pos)

    def test_no_llm_path_never_raises_on_a_themed_query(self, no_key, monkeypatch):
        """Whatever the themed query says, the fallback answers — and with a
        category, not with whatever bare keyword ILIKE happens to hit."""
        monkeypatch.setattr(jev, "ask", _explode)
        for query in (
            QUERY,
            "костёлы и замки Новогрудка",
            "музеи и кафе Гродно",
            "руины крепостей по области",
        ):
            res = extract_intent(query)          # must not raise
            assert res.source == "regex"
            assert res.decision.categories_pos, query

    def test_query_without_a_category_word_gets_an_honest_empty_set(self, no_key, monkeypatch):
        """Nothing stated → nothing invented: a category-less query keeps
        categories_pos empty instead of a keyword guess."""
        monkeypatch.setattr(jev, "ask", _explode)
        for query in ("куда сходить вечером", "что посмотреть в Гродно", "просто погулять"):
            assert extract_intent(query).decision.categories_pos == [], query

    def test_fallback_result_shape_unchanged(self, no_key):
        res = fallback_intent(QUERY)
        assert res.source == "regex"
        assert res.confidence == 0.0
        assert res.raw_response is None
        assert res.decision.intent_type in constants.INTENT_TYPES

    # ── The map is reused, never duplicated ─────────────────────────────────

    def test_inverted_index_is_the_shared_map_turned_around(self):
        """The fallback's lookup must be exactly resolve.CATEGORY_SYNONYMS,
        inverted — no second taxonomy that can drift from the first."""
        assert _KEYWORD_TO_CATEGORY == {
            form: cat
            for cat, forms in CATEGORY_SYNONYMS.items()
            for form in forms
            if " " not in form
        }

    def test_every_taxonomy_category_is_reachable(self):
        """A category whose surface forms are all missing from the map would
        be silently unextractable — fail loud instead."""
        assert set(CATEGORY_SYNONYMS) == set(constants.CATEGORIES)
        inverted_cats = set(_KEYWORD_TO_CATEGORY.values())
        assert inverted_cats == set(constants.CATEGORIES)

    def test_no_form_is_registered_twice(self):
        """A form under two categories is ambiguous — the map keeps them
        disjoint (the inversion would silently pick one)."""
        seen: dict[str, str] = {}
        for cat, forms in CATEGORY_SYNONYMS.items():
            for form in forms:
                assert form not in seen, f"{form!r} under both {seen.get(form)} and {cat}"
                seen[form] = cat


# ─────────────────────────────────────────────────────────────────────────────
# Time budget: only what the query itself states
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackTimeBudget:

    @pytest.mark.parametrize(
        "query,minutes",
        [
            ("погулять за 3 часа", 180),        # the phrase the task names
            ("погулять 2 часа по костёлам", 120),
            ("на 90 минут по замкам", 90),
            ("на полдня", 240),
            ("на весь день", 480),
        ],
    )
    def test_stated_durations_survive(self, no_key, query, minutes):
        assert fallback_intent(query).decision.time_budget_minutes == minutes

    @pytest.mark.parametrize(
        "query",
        [
            QUERY,                          # no phrase → no budget
            "просто погулять",
            "хочу в воскресенье",           # a bare "день" is not a budget
        ],
    )
    def test_no_phrase_no_budget(self, no_key, query):
        assert fallback_intent(query).decision.time_budget_minutes is None

    def test_budget_and_categories_together(self, no_key):
        d = fallback_intent("погулять по замкам Гродно за 3 часа").decision
        assert d.categories_pos == ["замок"]
        assert d.time_budget_minutes == 180


# ─────────────────────────────────────────────────────────────────────────────
# Named places: DB-driven, unchanged
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackNamedPlaces:

    def test_named_places_still_extracted_without_an_llm(self, no_key):
        d = fallback_intent(QUERY).decision
        assert "Гродно" in d.named_places
        # Region names are still filtered out (same regex as the Jev path).
        assert fallback_intent("достопримечательности Гродненской области").decision.named_places == []


# ─────────────────────────────────────────────────────────────────────────────
# With a stubbed LLM: the Jev path answers, the fallback is NOT used
# ─────────────────────────────────────────────────────────────────────────────

class TestStubbedLlmPathUnchanged:

    def test_fallback_is_not_used_when_a_stubbed_llm_answers(self, with_key, monkeypatch):
        monkeypatch.setattr(jev, "ask", _mock_jev_ask)
        monkeypatch.setattr(intent_mod, "fallback_intent", _explode)
        res = extract_intent(QUERY)
        assert res.source == "jev"
        assert res.raw_response is not None

    def test_categories_come_from_the_model_not_the_map(self, with_key, monkeypatch):
        """A stubbed LLM that scores замок high drives categories_pos — the
        deterministic map must not fire on the Jev path."""
        def scored(_query, _questions):
            answers = _mock_jev_ask(_query, _questions)
            answers["cat_замок"] = {"noul": 0.98, "confidence": 0.9}
            return answers
        monkeypatch.setattr(jev, "ask", scored)
        d = extract_intent("что посмотреть в Гродно").decision   # no category word
        assert d.categories_pos == ["замок"]                      # from the model

    def test_model_answers_drive_the_rest_of_the_decision(self, with_key, monkeypatch):
        monkeypatch.setattr(jev, "ask", _mock_jev_ask)
        d = extract_intent(QUERY).decision
        assert d.intent_type == "themed"
        assert d.search_scope == "town"
        assert d.era_hint == "any"
        assert d.party_type == "solo"
        assert d.named_places == ["Хочу", "Гродно"]
        # The mock scored 0 hours — no budget, exactly as before.
        assert d.time_budget_minutes is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

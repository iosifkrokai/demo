"""The interpretation layer's contracts: deterministic intent, agent hand-off.

Pins step 1 (intent / requirements) with no network and no model.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.planner import intent as intent_mod
from agent.planner.intent import (
    _KEYWORD_TO_CATEGORY,
    _SURFACE_FORMS,
    build_requirements,
    extract_intent,
    fallback_intent,
    intent_from_requirements,
)
from agent.planner.resolve import CATEGORY_SYNONYMS
from contracts.planner import GenerateReq
from core.config import openrouter_api_key, settings
from domain import constants
from domain.requirements import PartyComposition, Requirement, TripRequirements

QUERY = "Хочу погулять по замкам Гродно"


@pytest.fixture
def no_key(monkeypatch):
    """A process without an OpenRouter key: the env var AND the settings snapshot are
    cleared — otherwise a key from the developer's shell leaks into the test."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None, raising=False)
    assert openrouter_api_key() is None


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-test-key", raising=False)
    assert openrouter_api_key() is not None


def _explode(*_a, **_kw):
    raise AssertionError("no model may be consulted on this path")


def _agent_contract(*, source: str = "llm") -> TripRequirements:
    """A hand-built contract, as the interpretation agent would return it."""
    return TripRequirements(
        raw_query="что посмотреть в Гродно",
        party=PartyComposition(adults=2),
        budget_minutes=None,
        requirements=[
            Requirement(kind="interest", strength="soft", code="замок", label="замок"),
        ],
        source=source,  # type: ignore[arg-type]
    )


class TestFallbackCategories:

    def test_the_live_query_extracts_its_category(self, no_key):
        """The exact degraded request that answered with a canteen and a toilet:
        «замкам» → замок, so retrieval steers at castles."""
        d = extract_intent(QUERY).decision
        assert d.categories_pos == ["замок"]

    @pytest.mark.parametrize(
        "query,expected",
        [
            ("погулять по замкам Гродно", "замок"),
            ("замки Гродно", "замок"),
            ("старый замок", "замок"),
            ("руины замка", "замок"),
            ("замку не хватает ухода", "замок"),
            ("крепость Гродно", "замок"),
            ("крепости в области", "замок"),
            ("костёлы Гродно", "костёл"),
            ("костёлам Новогрудка", "костёл"),
            ("костел без подъезда", "костёл"),
            ("храмы города", "храм"),
            ("храма на горе", "храм"),
            ("кирхи Гродно", "храм"),
            ("синагогу посмотреть", "храм"),
            ("каплицы старого города", "храм"),
            ("монастыри области", "монастырь"),
            ("монастыря не видно", "монастырь"),
            ("музеи Гродно", "музей"),
            ("музея хватит на час", "музей"),
            ("где кафе рядом", "кафе"),
            ("кофейня в центре", "кафе"),
            ("кофейне не открыться", "кафе"),
            ("туалет на маршруте", "туалет"),
            ("уборная рядом", "туалет"),
            ("гостиница у вокзала", "гостиница"),
            ("хостела дешевле", "гостиница"),
            ("отеля с видом", "гостиница"),
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
    def test_categories_from_query_forms(self, no_key, query, expected):
        """Singular/plural/case forms and synonyms all resolve through the map."""
        d = extract_intent(query).decision
        assert expected in d.categories_pos, query

    def test_multiple_categories_in_one_query(self, no_key):
        d = extract_intent("костёлы и замки Новогрудка").decision
        assert {"костёл", "замок"} <= set(d.categories_pos)

    def test_no_llm_path_never_raises_on_a_themed_query(self, no_key):
        """Whatever the themed query says, the deterministic reader answers — with a
        category, not with whatever bare keyword ILIKE happens to hit."""
        for query in (
            QUERY,
            "костёлы и замки Новогрудка",
            "музеи и кафе Гродно",
            "руины крепостей по области",
        ):
            res = extract_intent(query)
            assert res.source == "regex"
            assert res.decision.categories_pos, query

    def test_query_without_a_category_word_gets_an_honest_empty_set(self, no_key):
        """Nothing stated → nothing invented: a category-less query keeps
        categories_pos empty instead of a keyword guess."""
        for query in ("куда сходить вечером", "что посмотреть в Гродно", "просто погулять"):
            assert extract_intent(query).decision.categories_pos == [], query

    def test_fallback_result_shape_unchanged(self, no_key):
        res = fallback_intent(QUERY)
        assert res.source == "regex"
        assert res.confidence == 0.0
        assert res.raw_response is None
        assert res.decision.intent_type in constants.INTENT_TYPES

    def test_inverted_index_is_the_shared_map_turned_around(self):
        """The fallback's lookup must be exactly resolve.CATEGORY_SYNONYMS,
        inverted — no second taxonomy that can drift from the first."""
        assert {
            form: cat
            for cat, forms in CATEGORY_SYNONYMS.items()
            for form in forms
            if " " not in form
        } == _KEYWORD_TO_CATEGORY

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

    def test_grodno_family_toilet_query_extracts_toilet_category(self, no_key):
        """Grodno family query with toilet phrase extracts туалет as optional
        category and does not make sightseeing categories exclusive."""
        query = "Гродно семья чтобы туалеты по пути были"
        d = extract_intent(query).decision
        assert "туалет" in d.categories_pos, "туалет category should be extracted"
        sightseeing = {"замок", "костёл", "церковь", "монастырь", "дворец", "усадьба",
                       "парк", "музей", "памятник", "храм", "архитектура", "инфраструктура", "кладбище"}
        assert not (sightseeing & set(d.categories_neg)), "sightseeing categories must not be exclusive"


class TestFallbackTimeBudget:

    @pytest.mark.parametrize(
        "query,minutes",
        [
            ("погулять за 3 часа", 180),
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
            QUERY,
            "просто погулять",
            "хочу в воскресенье",
        ],
    )
    def test_no_phrase_no_budget(self, no_key, query):
        assert fallback_intent(query).decision.time_budget_minutes is None

    def test_budget_and_categories_together(self, no_key):
        d = fallback_intent("погулять по замкам Гродно за 3 часа").decision
        assert d.categories_pos == ["замок"]
        assert d.time_budget_minutes == 180


class TestFallbackNamedPlaces:

    def test_named_places_still_extracted_without_an_llm(self, no_key):
        d = fallback_intent(QUERY).decision
        assert "Гродно" in d.named_places
        assert fallback_intent("достопримечательности Гродненской области").decision.named_places == []

    def test_discovery_query_pins_no_categories_named_place_town_scope_no_budget(self, no_key):
        """A pure discovery query like «достопримечательности Гродно» has no category
        keywords, extracts the named place, defaults to town scope, invents no budget."""
        d = fallback_intent("достопримечательности Гродно").decision
        assert d.categories_pos == []
        assert "Гродно" in d.named_places
        assert d.search_scope == "town"
        assert d.intent_type == "discovery"
        assert d.time_budget_minutes is None


class TestFallbackEnglish:

    def test_english_categories_resolve_through_the_shared_map(self, no_key):
        """The no-LLM path used to drop an English query entirely — «castles»
        now reads off the same taxonomy the Russian forms do."""
        assert "замок" in fallback_intent("castles in Grodno").decision.categories_pos
        assert "костёл" in fallback_intent("cathedrals of Grodno").decision.categories_pos
        assert "туалет" in fallback_intent("a walk with a toilet on the way").decision.categories_pos
        assert "кафе" in fallback_intent("coffee near the old town").decision.categories_pos

    def test_english_word_numeral_budget(self, no_key):
        assert fallback_intent("a walk for two hours").decision.time_budget_minutes == 120

    def test_english_still_extracts_nothing_when_nothing_is_stated(self, no_key):
        assert fallback_intent("what to see").decision.categories_pos == []

    def test_surface_map_keeps_russian_registrations(self):
        """A form both maps carry ("wc") must keep its Russian registration, and
        every Russian taxonomy form must stay reachable."""
        for form, cat in _KEYWORD_TO_CATEGORY.items():
            assert _SURFACE_FORMS[form] == cat
        assert set(constants.CATEGORIES) <= set(_SURFACE_FORMS.values())


class TestDegradedRequirements:

    def test_query_that_used_to_be_lost_now_reads_fully(self, no_key):
        """The measured bug, verbatim: solo party, no budget, no area."""
        q = "Погулять по старому Гродно с двумя детьми, туалет по пути, на два часа"
        tr = build_requirements(q, GenerateReq(query=q))
        assert tr.source == "fallback"
        assert tr.party.children == 2
        assert tr.party.children_ages == []
        assert tr.budget_minutes == 120
        assert "grodno-old-town" in tr.areas
        assert "туалет" in tr.soft_service_codes()

    def test_english_equivalents_produce_the_same_shape(self, no_key):
        q = "Walk around old Grodno with two children, toilet on the way, for two hours"
        tr = build_requirements(q, GenerateReq(query=q, locale="en"))
        assert tr.source == "fallback"
        assert tr.party.children == 2
        assert tr.budget_minutes == 120
        assert "grodno-old-town" in tr.areas
        assert tr.source == "fallback"

    def test_unprovable_request_is_an_unknown_not_a_requirement(self, no_key):
        q = "прогулка без лестниц по старому городу"
        tr = build_requirements(q, GenerateReq(query=q))
        assert "step_free" in tr.unknowns
        assert all(r.status != "satisfied" for r in tr.requirements)

    def test_no_key_means_the_deterministic_contract(self, no_key, monkeypatch):
        """With no key the agent cannot run, so build_requirements must produce
        the deterministic contract — and must not run a model."""
        from agent.planner import agent_interpret as ai
        monkeypatch.setattr(
            ai, "_run_agent",
            lambda *a, **k: pytest.fail("no model may be run without a key"),
        )
        tr = build_requirements(
            "что посмотреть в Гродно", GenerateReq(query="что посмотреть в Гродно")
        )
        assert tr.source == "fallback"


class TestAgentContractPath:

    def test_agent_contract_is_used_when_the_agent_answers(self, with_key, monkeypatch):
        """When the agent returns a contract, build_requirements uses it —
        source is "llm" and the contract's own categories drive the result."""
        monkeypatch.setattr(intent_mod, "_agent_contract", lambda *a, **k: _agent_contract())
        tr = build_requirements(
            "что посмотреть в Гродно", GenerateReq(query="что посмотреть в Гродно")
        )
        assert tr.source == "llm"
        assert tr.interest_codes() == ["замок"]

    def test_intent_from_requirements_follows_the_contract(self):
        """The IntentResult derived from a contract carries the contract's
        categories and named places — the plan is built from the reading."""
        contract = _agent_contract()
        contract.requirements.append(
            Requirement(kind="must_visit", name="Мир", label="Мир")
        )
        intent = intent_from_requirements(contract, "что посмотреть в Гродно")
        assert intent.source == "agent"
        assert intent.decision.categories_pos == ["замок"]
        assert "Мир" in intent.decision.named_places

    def test_extract_intent_is_always_deterministic(self, with_key):
        """`extract_intent` never calls a model — source is always "regex", key or not.
        Free-text meaning is the agent's job, reached through build_requirements."""
        assert extract_intent(QUERY).source == "regex"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

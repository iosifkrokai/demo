"""W2 — `build_requirements`: the single interpretation entry point.

Pins the contract that turns a tourist's request (RU or EN) plus the explicit
UI filters into `agent.requirements.TripRequirements`:

  * party composition carries a child COUNT and never an invented age;
  * the time budget is only the one the user stated (digit or word numeral);
  * a service is HARD when the text obliges it and SOFT otherwise, each with
    its verbatim provenance span;
  * "старый город" binds to a known area — not to the adjective "старый";
  * an explicit UI filter beats any text reading;
  * what the data cannot prove ("без лестниц") lands in `unknowns`, never in a
    satisfied requirement;
  * the no-key fallback produces the same shape as the LLM path.

The file is deterministic and offline: no DB, no network. When the model path is
exercised, the interpretation agent's contract is stubbed by monkeypatching
`intent_mod._agent_contract`, so no key and no network are needed.
"""

from __future__ import annotations

import os
import sys
from typing import Literal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import constants
from agent.config import openrouter_api_key, settings
from agent.models import GenerateReq
from agent.planner import intent as intent_mod
from agent.planner.intent import build_requirements
from agent.requirements import PartyComposition, Requirement, TripRequirements

RU = "ru"
EN = "en"

# The acceptance scenario (spec §9.1) and the measured degraded bug, verbatim.
RU_ACCEPT = (
    "Погулять по старому Гродно с двумя детьми, "
    "туалет обязательно, кафе если по пути, на два часа"
)
RU_BUG = "Погулять по старому Гродно с двумя детьми, туалет по пути, на два часа"
EN_ACCEPT = (
    "Walk around old Grodno with two children, "
    "toilet on the way, for two hours"
)


# ── Fixtures: the OpenRouter key is the switch between modes ─────────────────

@pytest.fixture
def no_key(monkeypatch):
    """A process without a key: env var AND the settings snapshot are cleared,
    so a developer's shell key cannot leak in and exercise the wrong branch."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None, raising=False)
    assert openrouter_api_key() is None


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "«redacted:sk-…»")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "«redacted:sk-…»", raising=False)
    assert openrouter_api_key() is not None


def _req(query: str, locale: Literal["ru", "en"] = RU, **kw) -> GenerateReq:
    return GenerateReq(query=query, locale=locale, **kw)


def _agent_contract(*, source: str = "llm", codes: tuple[str, ...] = ()) -> TripRequirements:
    """A contract shaped exactly as the interpretation agent returns one."""
    return TripRequirements(
        locale="ru",
        raw_query="stub",
        party=PartyComposition(),
        requirements=[
            Requirement(kind="interest", strength="soft", code=code, label=code)
            for code in codes
        ],
        source=source,  # type: ignore[arg-type]
    )


# ─────────────────────────────────────────────────────────────────────────────
# The acceptance scenario and the measured bug
# ─────────────────────────────────────────────────────────────────────────────

class TestAcceptanceScenarioRu:

    def test_party_is_two_children_with_no_invented_age(self, no_key):
        tr = build_requirements(RU_ACCEPT, _req(RU_ACCEPT))
        assert tr.party.children == 2
        assert tr.party.children_ages == []          # never guessed

    def test_budget_is_the_stated_two_hours(self, no_key):
        tr = build_requirements(RU_ACCEPT, _req(RU_ACCEPT))
        assert tr.budget_minutes == 120

    def test_old_grodno_is_an_area_not_the_word_stary(self, no_key):
        tr = build_requirements(RU_ACCEPT, _req(RU_ACCEPT))
        assert tr.areas == ["grodno-old-town"]

    def test_toilet_is_hard_and_cafe_is_soft(self, no_key):
        tr = build_requirements(RU_ACCEPT, _req(RU_ACCEPT))
        assert "туалет" in tr.hard_service_codes()
        assert "кафе" in tr.soft_service_codes()
        assert "кафе" not in tr.hard_service_codes()

    def test_each_requirement_keeps_its_provenance_span(self, no_key):
        tr = build_requirements(RU_ACCEPT, _req(RU_ACCEPT))
        by_code = {r.code: r for r in tr.requirements}
        assert by_code["туалет"].text == "туалет обязательно"
        assert by_code["кафе"].text == "кафе если по пути"
        assert by_code["туалет"].source == "text"

    def test_degraded_source_is_fallback(self, no_key):
        tr = build_requirements(RU_ACCEPT, _req(RU_ACCEPT))
        assert tr.source == "fallback"

    def test_returns_the_frozen_contract(self, no_key):
        tr = build_requirements(RU_ACCEPT, _req(RU_ACCEPT))
        assert isinstance(tr, TripRequirements)
        assert tr.raw_query == RU_ACCEPT
        assert tr.locale == "ru"


class TestMeasuredBugFixed:
    """The offline bug: party_type=solo, no budget, "старый город" not an area."""

    def test_children_are_not_solo(self, no_key):
        tr = build_requirements(RU_BUG, _req(RU_BUG))
        assert tr.party.children == 2

    def test_word_numeral_budget_is_read(self, no_key):
        """«на два часа» is a two-hour budget — the digit regex alone missed it."""
        tr = build_requirements(RU_BUG, _req(RU_BUG))
        assert tr.budget_minutes == 120

    def test_old_town_is_an_area(self, no_key):
        tr = build_requirements(RU_BUG, _req(RU_BUG))
        assert "grodno-old-town" in tr.areas

    def test_toilet_on_the_way_is_a_soft_service(self, no_key):
        tr = build_requirements(RU_BUG, _req(RU_BUG))
        assert "туалет" in tr.soft_service_codes()

    def test_english_equivalent_is_not_empty(self, no_key):
        tr = build_requirements(EN_ACCEPT, _req(EN_ACCEPT, EN))
        assert tr.party.children == 2
        assert tr.budget_minutes == 120
        assert "grodno-old-town" in tr.areas
        assert "туалет" in tr.soft_service_codes()
        assert tr.source == "fallback"


# ─────────────────────────────────────────────────────────────────────────────
# Areas: a known slug, never the bare adjective
# ─────────────────────────────────────────────────────────────────────────────

class TestAreas:

    def test_old_grodno_binds_to_the_verified_area(self, no_key):
        q = "Погулять по старому Гродно"
        assert build_requirements(q, _req(q)).areas == ["grodno-old-town"]

    def test_old_town_without_a_town_binds_to_grodno(self, no_key):
        q = "прогулка по старому городу"
        assert "grodno-old-town" in build_requirements(q, _req(q)).areas

    def test_another_town_is_not_restricted_to_grodno_old_town(self, no_key):
        q = "Лида, замок и старый город, 2 часа"
        assert "grodno-old-town" not in build_requirements(q, _req(q)).areas

    def test_no_area_when_none_is_named(self, no_key):
        q = "замки Гродно"
        assert build_requirements(q, _req(q)).areas == []

    def test_english_old_town(self, no_key):
        q = "a walk through the old town of Grodno"
        assert "grodno-old-town" in build_requirements(q, _req(q, EN)).areas


# ─────────────────────────────────────────────────────────────────────────────
# Party: count known, age never invented
# ─────────────────────────────────────────────────────────────────────────────

class TestPartyComposition:

    @pytest.mark.parametrize(
        "query,children",
        [
            ("погулять с двумя детьми", 2),
            ("прогулка с двумя детьми по центру", 2),
            ("двое детей и старый город", 2),
            ("с тремя детьми", 3),
            ("с 2 детьми", 2),
            ("двое ребят", None),          # "ребят" is not a stated child word
            ("семья погулять", None),      # no number stated → no count, not 0
        ],
    )
    def test_ru_child_count(self, no_key, query, children):
        tr = build_requirements(query, _req(query))
        assert tr.party.children == children

    @pytest.mark.parametrize(
        "query,children",
        [
            ("walk with two children", 2),
            ("a trip with 3 kids", 3),
            ("for the family", None),      # no number → None, never a guess
            ("with one child", 1),
        ],
    )
    def test_en_child_count(self, no_key, query, children):
        tr = build_requirements(query, _req(query, EN))
        assert tr.party.children == children

    def test_no_age_is_ever_invented(self, no_key):
        for q in ("с двумя детьми", "with two children", "двое детей"):
            loc = EN if q[0].isascii() else RU
            tr = build_requirements(q, _req(q, loc))
            assert tr.party.children_ages == [], q

    def test_stated_ages_are_kept_and_give_the_count(self, no_key):
        tr = build_requirements("прогулка с детьми 5 и 9 лет", _req("прогулка с детьми 5 и 9 лет"))
        assert tr.party.children_ages == [5, 9]
        assert tr.party.children == 2          # the ages enumerate the children

    def test_adults_count_when_stated(self, no_key):
        tr = build_requirements("двое взрослых и двое детей", _req("двое взрослых и двое детей"))
        assert tr.party.adults == 2
        assert tr.party.children == 2

    def test_a_budget_is_not_an_age(self, no_key):
        """"2 часа" must never leak into children_ages."""
        tr = build_requirements("погулять 2 часа с ребёнком 6 лет", _req("погулять 2 часа с ребёнком 6 лет"))
        assert tr.party.children_ages == [6]
        assert tr.budget_minutes == 120

    def test_mobility_is_read_from_text_not_inferred_from_size(self, no_key):
        tr = build_requirements("прогулка с двумя детьми в коляске", _req("прогулка с двумя детьми в коляске"))
        assert "stroller" in tr.party.mobility
        # size alone proves nothing about mobility
        plain = build_requirements("прогулка с двумя детьми", _req("прогулка с двумя детьми"))
        assert plain.party.mobility == []


# ─────────────────────────────────────────────────────────────────────────────
# Budget: only what the text states
# ─────────────────────────────────────────────────────────────────────────────

class TestBudget:

    @pytest.mark.parametrize(
        "query,minutes",
        [
            ("погулять за 3 часа", 180),
            ("погулять 2 часа", 120),
            ("на два часа по старому городу", 120),
            ("на полтора часа", 90),
            ("на 90 минут", 90),
            ("на полдня", 240),
            ("на весь день", 480),
            ("for two hours", 120),
            ("for 90 minutes", 90),
            ("a half day", 240),
            ("the whole day", 480),
        ],
    )
    def test_stated_durations(self, no_key, query, minutes):
        tr = build_requirements(query, _req(query))
        assert tr.budget_minutes == minutes

    @pytest.mark.parametrize(
        "query",
        ["просто погулять", "что посмотреть в Гродно", "хочу в воскресенье"],
    )
    def test_no_statement_no_budget(self, no_key, query):
        assert build_requirements(query, _req(query)).budget_minutes is None

    def test_budget_is_clamped_to_the_contract_bounds(self, no_key):
        tr = build_requirements("погулять 20 часов", _req("погулять 20 часов"))
        assert tr.budget_minutes == constants.MAX_BUDGET_MIN


# ─────────────────────────────────────────────────────────────────────────────
# Hard vs soft, provenance, must-visit, avoid, interests
# ─────────────────────────────────────────────────────────────────────────────

class TestHardSoftAndProvenance:

    @pytest.mark.parametrize(
        "query,code,strength",
        [
            ("нужен туалет по маршруту", "туалет", "hard"),
            ("туалет обязательно", "туалет", "hard"),
            ("по пути должен быть туалет, это необходимо", "туалет", "hard"),
            ("туалет по пути", "туалет", "soft"),
            ("кафе если по пути", "кафе", "soft"),
            ("хотелось бы кофейню рядом", "кафе", "soft"),
            ("must have a toilet", "туалет", "hard"),
            ("toilet on the way", "туалет", "soft"),
            ("maybe a cafe", "кафе", "soft"),
        ],
    )
    def test_service_strength(self, no_key, query, code, strength):
        tr = build_requirements(query, _req(query))
        svc = {r.code: r.strength for r in tr.requirements if r.kind == "service"}
        assert svc.get(code) == strength, (query, svc)

    def test_a_requirement_carries_its_fragment(self, no_key):
        query = "погулять, туалет обязательно, и всё"
        tr = build_requirements(query, _req(query))
        svc = next(r for r in tr.requirements if r.code == "туалет")
        assert svc.text == "туалет обязательно"

    def test_named_place_becomes_must_visit(self, no_key):
        query = "хочу посмотреть Мирский замок"
        tr = build_requirements(query, _req(query))
        assert "Мирский" in tr.must_visit_names() or "Мирский замок" in tr.must_visit_names()

    def test_taxonomy_words_are_not_place_names(self, no_key):
        query = "Замки и костёлы Новогрудка за 3 часа"
        tr = build_requirements(query, _req(query))
        assert "Замки" not in tr.must_visit_names()
        assert "Костёлы" not in tr.must_visit_names()

    def test_query_verb_is_not_a_place_name(self, no_key):
        tr = build_requirements("Погулять по старому Гродно", _req("Погулять по старому Гродно"))
        assert "Погулять" not in tr.must_visit_names()
        assert "старый" not in [n.lower() for n in tr.must_visit_names()]

    def test_interests_are_codes(self, no_key):
        tr = build_requirements("замки и костёлы Гродно", _req("замки и костёлы Гродно"))
        assert {"замок", "костёл"} <= set(tr.interest_codes())

    def test_avoid_is_a_hard_restriction(self, no_key):
        tr = build_requirements("погулять без замков", _req("погулять без замков"))
        assert "замок" in tr.avoid_codes()
        avoid = next(r for r in tr.requirements if r.kind == "avoid")
        assert avoid.strength == "hard"
        assert avoid.text and "замков" in avoid.text

    def test_english_avoid(self, no_key):
        tr = build_requirements("walk without museums", _req("walk without museums", EN))
        assert "музей" in tr.avoid_codes()


# ─────────────────────────────────────────────────────────────────────────────
# Unknowns: what cannot be proven is never satisfied
# ─────────────────────────────────────────────────────────────────────────────

class TestUnknowns:

    def test_no_stairs_is_unknown_not_satisfied(self, no_key):
        tr = build_requirements("прогулка без лестниц", _req("прогулка без лестниц"))
        assert "step_free" in tr.unknowns
        # and it did NOT become a satisfied requirement
        assert all(r.status != "satisfied" for r in tr.requirements)

    def test_step_free_english(self, no_key):
        tr = build_requirements("a step-free walk", _req("a step-free walk", EN))
        assert "step_free" in tr.unknowns

    def test_wheelchair_request_is_reported_unknown(self, no_key):
        tr = build_requirements("прогулка для инвалидной коляски", _req("прогулка для инвалидной коляски"))
        assert "wheelchair" in tr.party.mobility
        assert "wheelchair_accessible" in tr.unknowns

    def test_unknowns_empty_when_nothing_unprovable_asked(self, no_key):
        tr = build_requirements("замки Гродно", _req("замки Гродно"))
        assert tr.unknowns == []


# ─────────────────────────────────────────────────────────────────────────────
# Explicit UI filters win over a text guess
# ─────────────────────────────────────────────────────────────────────────────

class TestExplicitUiWins:

    def test_ui_child_count_beats_the_text(self, no_key):
        tr = build_requirements(RU_BUG, _req(RU_BUG, party_children=1))
        assert tr.party.children == 1

    def test_ui_budget_beats_the_text(self, no_key):
        tr = build_requirements(RU_BUG, _req(RU_BUG, time_budget_minutes=30))
        assert tr.budget_minutes == 30

    def test_ui_zero_means_no_limit_even_if_text_states_one(self, no_key):
        tr = build_requirements(RU_BUG, _req(RU_BUG, time_budget_minutes=0))
        assert tr.budget_minutes is None

    def test_ui_hard_service_beats_a_soft_text_reading(self, no_key):
        tr = build_requirements(RU_BUG, _req(RU_BUG, hard_services=["туалет"]))
        services = [r for r in tr.requirements if r.kind == "service" and r.code == "туалет"]
        assert len(services) == 1                      # no duplicate
        assert services[0].strength == "hard"
        assert services[0].source == "ui"

    def test_ui_interests_and_avoid(self, no_key):
        tr = build_requirements("погулять", _req("погулять", interests=["музей"], avoid=["костёл"]))
        assert "музей" in tr.interest_codes()
        assert "костёл" in tr.avoid_codes()
        ui_codes = {(r.kind, r.code) for r in tr.requirements if r.source == "ui"}
        assert ("interest", "музей") in ui_codes

    def test_ui_ages_and_mobility_are_kept(self, no_key):
        tr = build_requirements("погулять с детьми", _req("погулять с детьми",
                                                         party_children=2,
                                                         party_children_ages=[4, 7],
                                                         mobility=["stroller"]))
        assert tr.party.children_ages == [4, 7]
        assert tr.party.mobility == ["stroller"]

    def test_source_is_explicit_when_only_ui_produced_requirements(self, no_key):
        tr = build_requirements("погулять", _req("погулять", hard_services=["туалет"]))
        assert tr.source == "explicit"

    def test_profile_and_result_mode_pass_through(self, no_key):
        tr = build_requirements("погулять", _req("погулять", profile="bicycle",
                                                 result_mode="catalogue",
                                                 round_trip=True))
        assert tr.costing == "bicycle"
        assert tr.result_mode == "catalogue"
        assert tr.round_trip is True


# ─────────────────────────────────────────────────────────────────────────────
# The agent path produces the contract, and degrades cleanly
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentPath:

    def test_agent_adds_the_category_the_word_map_missed(self, with_key, monkeypatch):
        """A bare discovery query has no category word; the agent's reading is
        what puts «замок» into the contract."""
        monkeypatch.setattr(
            intent_mod, "_agent_contract",
            lambda *a, **k: _agent_contract(codes=("замок",)),
        )
        tr = build_requirements("что посмотреть в Гродно", _req("что посмотреть в Гродно"))
        assert "замок" in tr.interest_codes()
        assert tr.source == "llm"

    def test_agent_and_ui_together_are_mixed(self, with_key, monkeypatch):
        """The agent layer already merges the UI filters and reports "mixed";
        build_requirements must not overwrite that provenance."""
        monkeypatch.setattr(
            intent_mod, "_agent_contract",
            lambda *a, **k: _agent_contract(source="mixed"),
        )
        tr = build_requirements(RU_BUG, _req(RU_BUG, hard_services=["туалет"]))
        assert tr.source == "mixed"

    def test_agent_outage_degrades_to_fallback(self, with_key, monkeypatch):
        """No agent reading (None) → the deterministic reading, with the UI
        filters and everything the text states intact."""
        monkeypatch.setattr(intent_mod, "_agent_contract", lambda *a, **k: None)
        tr = build_requirements(RU_BUG, _req(RU_BUG))
        assert tr.source == "fallback"
        assert tr.party.children == 2
        assert tr.budget_minutes == 120

    def test_agent_exception_degrades_to_fallback(self, with_key, monkeypatch):
        """An unexpected error in the agent layer must never fail the request."""
        def boom(*_a, **_kw):
            raise RuntimeError("simulated agent failure")
        monkeypatch.setattr(intent_mod, "_agent_contract", boom)
        tr = build_requirements(RU_BUG, _req(RU_BUG))
        assert tr.source == "fallback"

    def test_same_shape_as_offline(self, with_key, monkeypatch):
        """The agent path fills the same fields as the offline reading."""
        monkeypatch.setattr(
            intent_mod, "_agent_contract",
            lambda *a, **k: _agent_contract(codes=("замок",)),
        )
        online = build_requirements(RU_BUG, _req(RU_BUG))
        assert online.model_dump().keys() == TripRequirements(
            raw_query=RU_BUG
        ).model_dump().keys()


# ─────────────────────────────────────────────────────────────────────────────
# Robustness
# ─────────────────────────────────────────────────────────────────────────────

class TestRobustness:

    @pytest.mark.parametrize(
        "query",
        ["???", "ааа", "прогулка", "Walk", "старый город", "без", "туалет"],
    )
    def test_never_raises(self, no_key, query):
        assert isinstance(build_requirements(query, _req(query)), TripRequirements)

    def test_a_category_less_query_invents_nothing(self, no_key):
        tr = build_requirements("куда сходить вечером", _req("куда сходить вечером"))
        assert tr.interest_codes() == []
        assert tr.budget_minutes is None
        assert tr.party.children is None
        assert tr.areas == []

    def test_every_requirement_is_resolvable_by_the_contract_helpers(self, no_key):
        tr = build_requirements(RU_ACCEPT, _req(RU_ACCEPT))
        # the frozen accessors must run over whatever we produced
        assert tr.hard() or tr.soft()
        assert tr.public_requirements()
        for r in tr.requirements:
            assert r.status == "pending"          # nothing is proven at build time
            assert r.is_resolved() is False

    def test_module_exposes_the_entry_point(self):
        assert intent_mod.build_requirements is build_requirements

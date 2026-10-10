"""`build_requirements`: the single interpretation entry point.

Reading a free-text query is the model's job (``agent_interpret``); these tests pin
the contract AROUND the reading — the explicit UI controls win, the budget is
clamped, an explicit choice shows up as ``source="mixed"``, and a request the model
cannot read is refused rather than guessed at.
"""

from __future__ import annotations

import os
import sys
from typing import Literal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from contracts.planner import GenerateReq
from core.config import openrouter_api_key, settings
from core.errors import InterpretationUnavailable
from domain import constants
from domain.requirements import TripRequirements
from planner import intent as intent_mod
from planner.intent import build_requirements

RU = "ru"

RU_ACCEPT = (
    "Погулять по старому Гродно с двумя детьми, "
    "туалет обязательно, кафе если по пути, на два часа"
)
RU_BUG = "Погулять по старому Гродно с двумя детьми, туалет по пути, на два часа"


@pytest.fixture
def no_key(monkeypatch):
    """A process without a key: env var AND the settings snapshot are cleared,
    so a developer's shell key cannot leak in and exercise the wrong branch."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None, raising=False)
    assert openrouter_api_key() is None


def _req(query: str, locale: Literal["ru", "en"] = RU, **kw) -> GenerateReq:
    return GenerateReq(query=query, locale=locale, **kw)


class TestNoReaderRefuses:
    """No reading means no plan — never a keyword guess dressed up as one."""

    def test_no_key_refuses_the_request(self, no_key):
        with pytest.raises(InterpretationUnavailable):
            build_requirements(RU_BUG, _req(RU_BUG))

    def test_a_missing_reading_refuses_the_request(self, fake_llm, monkeypatch):
        monkeypatch.setattr(intent_mod, "_agent_contract", lambda *a, **k: None)
        with pytest.raises(InterpretationUnavailable):
            build_requirements(RU_BUG, _req(RU_BUG))

    def test_a_failed_reading_is_not_replaced_by_a_guess(self, fake_llm, monkeypatch):
        from agent import client as ai

        def boom(*_a, **_kw):
            raise RuntimeError("agent: tool search_places failed: db gone")

        monkeypatch.setattr(ai, "interpret_with_agent", boom)
        with pytest.raises(InterpretationUnavailable):
            build_requirements(RU_BUG, _req(RU_BUG))


class TestReadingReachesTheContract:
    """Whatever the model reads out of the text lands in the frozen contract."""

    def test_returns_the_frozen_contract(self, fake_llm):
        fake_llm.set()
        tr = build_requirements(RU_BUG, _req(RU_BUG))
        assert isinstance(tr, TripRequirements)
        assert tr.raw_query == RU_BUG
        assert tr.locale == "ru"

    def test_a_model_reading_fills_the_contract(self, fake_llm):
        query = "старый Гродно, двое детей 5 и 9 лет, два часа, туалет обязательно"
        fake_llm.set(
            children=2,
            children_ages=[5, 9],
            budget_minutes=120,
            areas=["grodno-old-town"],
            requirements=[{"kind": "service", "strength": "hard", "code": "туалет"}],
        )
        tr = build_requirements(query, _req(query))
        assert tr.party.children == 2
        assert tr.party.children_ages == [5, 9]
        assert tr.budget_minutes == 120
        assert tr.areas == ["grodno-old-town"]
        assert "туалет" in tr.hard_service_codes()
        assert tr.source == "llm"

    def test_reading_and_ui_together_are_mixed(self, fake_llm):
        fake_llm.set(requirements=[{"kind": "interest", "strength": "soft", "code": "замок"}])
        tr = build_requirements(RU_BUG, _req(RU_BUG, hard_services=["туалет"]))
        assert tr.source == "mixed"


class TestBudget:

    def test_a_stated_budget_reaches_the_contract(self, fake_llm):
        fake_llm.set(budget_minutes=120)
        assert build_requirements("погулять 2 часа", _req("погулять 2 часа")).budget_minutes == 120

    def test_budget_is_clamped_to_the_contract_bounds(self, fake_llm):
        fake_llm.set(budget_minutes=20 * 60)
        tr = build_requirements("погулять 20 часов", _req("погулять 20 часов"))
        assert tr.budget_minutes == constants.MAX_BUDGET_MIN

    def test_a_reading_without_a_budget_leaves_it_unset(self, fake_llm):
        fake_llm.set()
        assert build_requirements("просто погулять", _req("просто погулять")).budget_minutes is None


class TestExplicitUiWins:

    def test_ui_child_count_beats_the_reading(self, fake_llm):
        fake_llm.set(children=2)
        tr = build_requirements(RU_BUG, _req(RU_BUG, party_children=1))
        assert tr.party.children == 1

    def test_ui_budget_beats_the_reading(self, fake_llm):
        fake_llm.set(budget_minutes=120)
        tr = build_requirements(RU_BUG, _req(RU_BUG, time_budget_minutes=30))
        assert tr.budget_minutes == 30

    def test_ui_zero_means_no_limit_even_if_the_reading_states_one(self, fake_llm):
        fake_llm.set(budget_minutes=120)
        tr = build_requirements(RU_BUG, _req(RU_BUG, time_budget_minutes=0))
        assert tr.budget_minutes is None

    def test_ui_hard_service_beats_a_soft_reading(self, fake_llm):
        fake_llm.set(requirements=[{"kind": "service", "strength": "soft", "code": "туалет"}])
        tr = build_requirements(RU_BUG, _req(RU_BUG, hard_services=["туалет"]))
        services = [r for r in tr.requirements if r.kind == "service" and r.code == "туалет"]
        assert len(services) == 1
        assert services[0].strength == "hard"
        assert services[0].source == "ui"

    def test_ui_interests_and_avoid_survive(self, fake_llm):
        fake_llm.set()
        tr = build_requirements("погулять", _req("погулять", interests=["музей"], avoid=["костёл"]))
        assert "музей" in tr.interest_codes()
        assert "костёл" in tr.avoid_codes()
        ui_codes = {(r.kind, r.code) for r in tr.requirements if r.source == "ui"}
        assert ("interest", "музей") in ui_codes

    def test_ui_ages_and_mobility_are_kept(self, fake_llm):
        fake_llm.set()
        tr = build_requirements(
            "погулять с детьми",
            _req("погулять с детьми", party_children=2, party_children_ages=[4, 7],
                 mobility=["stroller"]),
        )
        assert tr.party.children_ages == [4, 7]
        assert tr.party.mobility == ["stroller"]

    def test_source_is_mixed_when_ui_produced_requirements(self, fake_llm):
        fake_llm.set()
        tr = build_requirements("погулять", _req("погулять", hard_services=["туалет"]))
        assert tr.source == "mixed"

    def test_profile_and_result_mode_pass_through(self, fake_llm):
        fake_llm.set()
        tr = build_requirements(
            "погулять",
            _req("погулять", profile="bicycle", result_mode="catalogue", round_trip=True),
        )
        assert tr.costing == "bicycle"
        assert tr.result_mode == "catalogue"
        assert tr.round_trip is True


class TestRobustness:

    @pytest.mark.parametrize(
        "query",
        ["???", "ааа", "прогулка", "Walk", "старый город", "без", "туалет"],
    )
    def test_never_raises_with_a_reading(self, fake_llm, query):
        fake_llm.set()
        assert isinstance(build_requirements(query, _req(query)), TripRequirements)

    def test_an_empty_reading_invents_nothing(self, fake_llm):
        fake_llm.set()
        tr = build_requirements("куда сходить вечером", _req("куда сходить вечером"))
        assert tr.interest_codes() == []
        assert tr.budget_minutes is None
        assert tr.party.children is None
        assert tr.areas == []

    def test_every_requirement_is_resolvable_by_the_contract_helpers(self, fake_llm):
        fake_llm.set(
            requirements=[
                {"kind": "service", "strength": "hard", "code": "туалет"},
                {"kind": "service", "strength": "soft", "code": "кафе"},
            ]
        )
        tr = build_requirements(RU_ACCEPT, _req(RU_ACCEPT))
        assert tr.hard() or tr.soft()
        assert tr.public_requirements()
        for r in tr.requirements:
            assert r.status == "pending"
            assert r.is_resolved() is False

    def test_module_exposes_the_entry_point(self):
        assert intent_mod.build_requirements is build_requirements

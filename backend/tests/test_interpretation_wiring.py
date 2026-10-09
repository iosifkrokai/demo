"""The interpretation wiring (spec 002 §4): the model proposes MEANING, the
deterministic verifier decides VERDICTS.

Four contracts, all offline — no key, no network, no DB:

1. no key → the deterministic reading answers, with honest provenance;
2. a model contract can never override a *visible* UI filter;
3. a tool/upstream failure in the interpretation layer degrades instead of
   turning the request into a 500;
4. a mandatory requirement the MODEL produced is decided by verify.py, never by
   the model that proposed it.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import main as agent_main
from agent.config import settings
from agent.errors import UpstreamUnavailable
from agent.models import Candidate, GenerateReq, ValidatedPlan
from agent.planner import intent as intent_mod
from agent.planner.intent import build_requirements
from agent.planner.pipeline import _interpretation
from agent.planner.verify import overall_status, verify
from agent.requirements import PartyComposition, Requirement, TripRequirements

RU = "Погулять по старому Гродно с двумя детьми, туалет обязательно, кафе если по пути"

GEOJSON = {
    "type": "LineString",
    "coordinates": [[23.82, 53.68], [23.825, 53.685]],
}


# Fixtures: the OpenRouter key is the switch between modes

@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None, raising=False)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key-not-real", raising=False)


def _contract(*requirements: Requirement, source: str = "llm") -> TripRequirements:
    return TripRequirements(
        locale="ru",
        raw_query=RU,
        party=PartyComposition(),
        requirements=list(requirements),
        source=source,  # type: ignore[arg-type]
    )


def _plan(*stops: Candidate) -> ValidatedPlan:
    return ValidatedPlan(
        route=list(stops),
        walk_seconds=0.0,
        visit_seconds=0,
        total_seconds=0,
        fits_budget=True,
    )


# (1) No key → deterministic reading, honest provenance

class TestNoKeyIsDeterministic:

    def test_no_key_produces_the_deterministic_contract(self, no_key):
        tr = build_requirements(RU, GenerateReq(query=RU, hard_services=["туалет"]))
        assert tr.source == "explicit"          # UI filter + text reading
        assert "туалет" in tr.hard_service_codes()
        assert tr.party.children == 2
        assert tr.areas == ["grodno-old-town"]

    def test_provenance_is_honest_on_both_paths(self, no_key, with_key, monkeypatch):
        """"fallback"/"explicit" when no model answered, "llm"/"mixed" when one
        did — the contract never claims a model that did not run."""
        det = build_requirements(RU, GenerateReq(query=RU))
        assert det.source == "fallback"

        monkeypatch.setattr(
            intent_mod, "_agent_contract",
            lambda *a, **k: _contract(source="llm"),
        )
        agent = build_requirements(RU, GenerateReq(query=RU))
        assert agent.source == "llm"

    def test_the_planner_reading_says_where_meaning_came_from(self, no_key, monkeypatch):
        tr = build_requirements(RU, GenerateReq(query=RU))
        intent = intent_mod.intent_from_requirements(tr, RU)
        assert intent.source == "fallback"
        # A query with no category word: only the model can supply one, so the
        # code that shows up is proof the contract drove the reading.
        bare = "что посмотреть в Гродно"
        monkeypatch.setattr(
            intent_mod, "_agent_contract",
            lambda *a, **k: _contract(
                Requirement(kind="interest", strength="soft", code="замок", label="замок")
            ),
        )
        tr = build_requirements(bare, GenerateReq(query=bare))
        intent = intent_mod.intent_from_requirements(tr, bare)
        assert intent.source == "agent"
        assert intent.decision.categories_pos == ["замок"]


# (2) A visible UI filter is never overridden by the model

class TestUiFilterWins:

    def test_model_omitting_a_ui_filter_does_not_lose_it(self, with_key, monkeypatch):
        """The toilet is switched ON in the UI; the model's contract does not
        mention it.  The filter survives — hard, and attributed to the UI."""
        monkeypatch.setattr(
            intent_mod, "_agent_contract",
            lambda *a, **k: _contract(
                Requirement(kind="interest", strength="soft", code="замок", label="замок")
            ),
        )
        tr = build_requirements(
            RU,
            GenerateReq(query=RU, hard_services=["туалет"], party_children=1,
                        time_budget_minutes=45),
        )
        svc = [r for r in tr.requirements if r.kind == "service" and r.code == "туалет"]
        assert len(svc) == 1
        assert svc[0].strength == "hard"
        assert svc[0].source == "ui"
        assert tr.party.children == 1        # UI scalar, not the text's 2
        assert tr.budget_minutes == 45       # UI budget, not the text's
        assert tr.source == "mixed"

    def test_model_downgrading_a_ui_filter_does_not_win(self, with_key, monkeypatch):
        """The model says the toilet is merely optional; the UI says mandatory.
        The UI reading stands and there is exactly one such requirement."""
        monkeypatch.setattr(
            intent_mod, "_agent_contract",
            lambda *a, **k: _contract(
                Requirement(kind="service", strength="soft", code="туалет",
                            label="туалет", source="text"),
            ),
        )
        tr = build_requirements(RU, GenerateReq(query=RU, hard_services=["туалет"]))
        svc = [r for r in tr.requirements if r.kind == "service" and r.code == "туалет"]
        assert len(svc) == 1
        assert svc[0].strength == "hard"
        assert svc[0].source == "ui"

    def test_model_avoiding_a_ui_interest_does_not_win(self, with_key, monkeypatch):
        """The UI turns «музей» ON; the model wants to avoid it.  A visible
        positive filter is not overridden by the model's reading."""
        monkeypatch.setattr(
            intent_mod, "_agent_contract",
            lambda *a, **k: _contract(
                Requirement(kind="avoid", strength="hard", code="музей", label="музей")
            ),
        )
        tr = build_requirements(RU, GenerateReq(query=RU, interests=["музей"]))
        assert "музей" not in tr.avoid_codes()
        assert "музей" in tr.interest_codes()


# (3) A tool/upstream failure degrades, never a 500

class TestFailuresDegrade:

    def test_a_tool_failure_degrades_to_the_deterministic_reading(
        self, with_key, monkeypatch
    ):
        """A bounded tool blowing up inside the agent must cost the reading, not
        the request: the deterministic contract answers, UI filters intact."""
        from agent.planner import agent_interpret as ai

        def boom(*_a, **_k):
            raise RuntimeError("tool search_places failed: db gone")

        monkeypatch.setattr(ai, "interpret_with_agent", boom)
        tr = build_requirements(RU, GenerateReq(query=RU, hard_services=["туалет"]))
        assert tr.source == "explicit"
        assert "туалет" in tr.hard_service_codes()

    def test_a_tool_failure_inside_the_agent_returns_no_contract(self, with_key, monkeypatch):
        """`interpret_with_agent` swallows a tool/model failure and returns None
        — a half-filled contract is never handed to the planner."""
        from agent.planner import agent_interpret as ai

        def boom(*_a, **_k):
            raise RuntimeError("tool services_near_route failed: db gone")

        monkeypatch.setattr(ai, "_run_agent", boom)
        assert ai.interpret_with_agent(RU, GenerateReq(query=RU)) is None

    def test_a_no_agent_reading_is_not_an_error(self, with_key, monkeypatch):
        monkeypatch.setattr(intent_mod, "_agent_contract", lambda *a, **k: None)
        tr = build_requirements(RU, GenerateReq(query=RU))
        assert tr.source == "fallback"

    def test_an_upstream_error_is_503_not_500(self):
        """The last-resort net: what escapes the planner is a typed 503."""
        def failing(**_kw):
            raise UpstreamUnavailable("valhalla: /route failed: 502")

        with pytest.raises(HTTPException) as exc:
            agent_main._call(failing)
        assert exc.value.status_code == 503
        assert "502" in exc.value.detail


# (4) verify.py decides, not the model

class TestVerifierDecides:

    def _mandatory_toilet(self) -> TripRequirements:
        """Exactly what the interpretation agent would hand over: a hard,
        model-produced requirement to have a toilet on the route."""
        return _contract(
            Requirement(kind="service", strength="hard", code="туалет",
                        label="туалет", text="туалет обязательно", source="text"),
        )

    def test_a_served_mandatory_requirement_is_verified_satisfied(self):
        tr = self._mandatory_toilet()
        plan = _plan(
            Candidate(id=1, name="Туалет у парка", category="туалет", lat=53.68, lon=23.82)
        )
        verify(tr, plan, GEOJSON)
        assert tr.requirements[0].status == "satisfied"
        assert tr.requirements[0].reason == "service_on_route"
        assert overall_status(tr) == "ready"

    def test_an_unserved_mandatory_requirement_is_verified_unmet(self):
        """The model asked for a toilet; the route has none.  The model does not
        get a vote — verify.py marks it unmet and the plan infeasible."""
        tr = self._mandatory_toilet()
        plan = _plan(
            Candidate(id=2, name="Кафе", category="кафе", lat=53.681, lon=23.821)
        )
        verify(tr, plan, GEOJSON)
        assert tr.requirements[0].status == "unmet"
        assert tr.requirements[0].reason == "hard_service_absent"
        assert overall_status(tr) == "infeasible"

    def test_missing_geometry_is_uncertain_never_satisfied(self):
        """No shape from Valhalla: a present stop is unproven, not satisfied."""
        tr = self._mandatory_toilet()
        plan = _plan(
            Candidate(id=1, name="Туалет у парка", category="туалет", lat=53.68, lon=23.82)
        )
        verify(tr, plan, None)
        assert tr.requirements[0].status == "uncertain"
        assert tr.requirements[0].reason == "geometry_missing"
        assert overall_status(tr) == "degraded"


# (5) The client-visible interpretation block, unmet included

class TestInterpretationBlock:
    """`RouteResponse.interpretation` — what the system understood, one place.

    The frontend renders chips from it, so it must be codes + numbers only, and
    a stated mandatory requirement that could not be met must appear in `unmet`
    with its reason — never a silent success.
    """

    def _mandatory_toilet(self) -> TripRequirements:
        return _contract(
            Requirement(kind="service", strength="hard", code="туалет",
                        label="туалет", text="туалет обязательно", source="text"),
            Requirement(kind="interest", strength="soft", code="костёл", label="костёл"),
        )

    def test_a_stated_mandatory_stop_that_cannot_be_met_is_explicit(self):
        """The owner's live bug: the same query sometimes planned a route with
        no toilet, silently.  The response must say so, with a reason code."""
        tr = self._mandatory_toilet()
        # A route that satisfies the interest but has no toilet at all.
        plan = _plan(
            Candidate(id=4, name="Фарный костёл", category="костёл", lat=53.68, lon=23.82)
        )
        verify(tr, plan, GEOJSON)
        block = _interpretation(tr, overall_status(tr))

        assert block.status == "infeasible"
        assert block.unmet, "an unsatisfied mandatory requirement must be listed"
        codes = [s.code for s in block.unmet]
        assert "туалет" in codes
        toilet = next(s for s in block.unmet if s.code == "туалет")
        assert toilet.strength == "hard"
        assert toilet.status == "unmet"
        assert toilet.reason == "hard_service_absent"
        # and the requirement that WAS met is not in the unmet list
        assert "костёл" not in codes

    def test_a_satisfied_request_has_an_empty_unmet_list(self):
        tr = self._mandatory_toilet()
        plan = _plan(
            Candidate(id=1, name="Туалет у парка", category="туалет", lat=53.68, lon=23.82),
            Candidate(id=4, name="Фарный костёл", category="костёл", lat=53.685, lon=23.825),
        )
        verify(tr, plan, GEOJSON)
        block = _interpretation(tr, overall_status(tr))
        assert block.status == "ready"
        assert block.unmet == []

    def test_the_block_is_codes_and_numbers_only(self):
        """No prose in the payload: the client localises every code itself."""
        tr = self._mandatory_toilet()
        verify(tr, _plan(), GEOJSON)
        block = _interpretation(tr, overall_status(tr))
        dumped = block.model_dump()
        # the fields the client needs for chips
        assert set(dumped) >= {
            "source", "locale", "status", "requirements", "unmet", "unknowns",
            "areas", "budget_minutes", "transport", "result_mode",
        }
        for s in dumped["requirements"]:
            assert set(s) == {
                "kind", "strength", "code", "name", "origin", "status",
                "reason", "place_ids",
            }

    def test_provenance_says_ui_or_model_or_parser(self, with_key, monkeypatch):
        """Each chip is attributed: a visible control, the model, or the parser."""
        monkeypatch.setattr(
            intent_mod, "_agent_contract",
            lambda *a, **k: _contract(
                Requirement(kind="service", strength="hard", code="туалет",
                            label="туалет", source="text"),
                source="llm",
            ),
        )
        tr = build_requirements(RU, GenerateReq(query=RU, interests=["музей"]))
        block = _interpretation(tr, "pending")
        assert block.source == "mixed"
        by_code = {s.code: s for s in block.requirements}
        assert by_code["музей"].origin == "ui"       # a visible control
        assert by_code["туалет"].origin == "agent"   # the model read the text

    def test_the_parser_is_named_when_no_model_answered(self, no_key):
        tr = build_requirements(RU, GenerateReq(query=RU))
        block = _interpretation(tr, "ready")
        assert block.source == "fallback"
        assert all(s.origin == "fallback" for s in block.requirements)

    def test_the_field_is_added_not_renamed(self):
        """Item-5 contract: the response grows, existing fields stay untouched."""
        from agent.models import RouteResponse

        fields = RouteResponse.model_fields
        for old in ("parsed", "points", "shape", "summary", "budget",
                    "explanation", "status", "requirements", "costing",
                    "changes", "debug"):
            assert old in fields
        assert "interpretation" in fields

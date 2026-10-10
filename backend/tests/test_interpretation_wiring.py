"""The interpretation wiring: the model proposes MEANING, the verifier decides VERDICTS.

Four contracts, all offline — no key, no network, no DB.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.models import PartyComposition, Requirement, TripRequirements
from api.deps import call_planner
from core.config import settings
from core.errors import InterpretationUnavailable, UpstreamUnavailable
from planner import intent as intent_mod
from planner.intent import build_requirements, reader_brief
from planner.models import Candidate, GenerateReq, ValidatedPlan
from planner.response import _interpretation
from planner.verify import overall_status, verify

RU = "Погулять по старому Гродно с двумя детьми, туалет обязательно, кафе если по пути"

GEOJSON = {
    "type": "LineString",
    "coordinates": [[23.82, 53.68], [23.825, 53.685]],
}


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


class TestNoReaderRefuses:

    def test_no_key_refuses_the_request(self, no_key):
        with pytest.raises(InterpretationUnavailable):
            build_requirements(RU, GenerateReq(query=RU, hard_services=["туалет"]))

    def test_a_reading_is_tagged_llm(self, fake_llm):
        """A model answered, so the contract says so — never a source it did not earn."""
        fake_llm.set()
        assert build_requirements(RU, GenerateReq(query=RU)).source == "llm"

    def test_the_planner_reading_says_where_meaning_came_from(self, fake_llm):
        bare = "что посмотреть в Гродно"
        fake_llm.set(requirements=[{"kind": "interest", "strength": "soft", "code": "замок"}])
        tr = build_requirements(bare, GenerateReq(query=bare))
        intent = intent_mod.intent_from_requirements(tr, bare)
        assert intent.source == "agent"
        assert intent.decision.categories_pos == ["замок"]


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
        assert tr.party.children == 1
        assert tr.budget_minutes == 45
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


class TestFailuresDegrade:

    def test_a_tool_failure_refuses_the_request(self, with_key, monkeypatch):
        """A bounded tool blowing up inside the agent costs the reading, and with
        no reading there is no contract: the request is refused, never guessed at."""
        from agent import client as ai

        def boom(*_a, **_k):
            raise RuntimeError("tool search_places failed: db gone")

        monkeypatch.setattr(ai, "interpret_with_agent", boom)
        with pytest.raises(InterpretationUnavailable):
            build_requirements(RU, GenerateReq(query=RU, hard_services=["туалет"]))

    def test_a_tool_failure_inside_the_agent_returns_no_contract(self, with_key, monkeypatch):
        """`interpret_with_agent` swallows a tool/model failure and returns None
        — a half-filled contract is never handed to the planner."""
        from agent import client as ai, runner as ai_runner

        def boom(*_a, **_k):
            raise RuntimeError("tool services_near_route failed: db gone")

        monkeypatch.setattr(ai_runner, "_run_agent", boom)
        assert ai.interpret_with_agent(RU, reader_brief(GenerateReq(query=RU))) is None

    def test_a_missing_reading_refuses_the_request(self, with_key, monkeypatch):
        monkeypatch.setattr(intent_mod, "_agent_contract", lambda *a, **k: None)
        with pytest.raises(InterpretationUnavailable):
            build_requirements(RU, GenerateReq(query=RU))

    def test_an_upstream_error_is_503_not_500(self):
        """The last-resort net: what escapes the planner is a typed 503."""
        def failing(**_kw):
            raise UpstreamUnavailable("valhalla: /route failed: 502")

        with pytest.raises(HTTPException) as exc:
            call_planner(failing)
        assert exc.value.status_code == 503
        assert "502" in exc.value.detail


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


class TestInterpretationBlock:
    """`RouteResponse.interpretation` — what the system understood, one place.

    Codes + numbers only; an unmet requirement appears in `unmet` with its reason.
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
        assert by_code["музей"].origin == "ui"
        assert by_code["туалет"].origin == "agent"

    def test_the_field_is_added_not_renamed(self):
        """The response grows; existing fields stay untouched."""
        from planner.models import RouteResponse

        fields = RouteResponse.model_fields
        for old in ("parsed", "points", "shape", "summary", "budget",
                    "explanation", "status", "requirements", "costing",
                    "changes", "debug"):
            assert old in fields
        assert "interpretation" in fields

"""PydanticAI interpretation layer — offline contract pins.

Everything here runs with no database, no network and no API key: the model is
a `FunctionModel` (PydanticAI's in-process fake) and the DB seams in
`agent/tools.py` are monkeypatched. What is pinned:

  * no key (or no SDK) → `interpret_with_agent` returns None and the
    deterministic `build_requirements` path keeps the explicit UI filters;
  * a fake model answer → a filled contract with hard/soft requirements,
    children count, provenance, and unresolvable asks in `unknowns`;
  * tool result caps, unknown-category dropping and DB failures never raise at
    the agent;
  * the agent is never consulted to declare a requirement satisfied — there is
    no `status` field in its output schema, and every requirement stays
    `pending` after interpretation.
"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest
from pydantic_ai import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import areas as areas_mod, tools, trace
from agent.config import settings
from agent.models import GenerateReq
from agent.planner import agent_interpret as ai
from agent.planner.intent import build_requirements

# The acceptance query from spec §9.1, abbreviated.
QUERY = "старый Гродно, двое детей 5 и 9 лет, два часа, туалет обязателен, кафе если по пути"

BOUNDED_TOOLS = {"find_areas", "search_places", "get_place_facts", "services_near_route"}

KEY_ENV = "OPENROUTER_API_KEY"
FAKE_KEY = "test-key-not-real"  # never a real credential


# Fixtures: no key, no DB, no network


class _FakeConn:
    """Stand-in for a psycopg connection — nothing in these tests touches a DB."""

    def close(self) -> None:
        pass


@pytest.fixture
def no_key(monkeypatch):
    """Remove the OpenRouter key from both sources of truth (env + settings)."""
    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)


@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setenv(KEY_ENV, FAKE_KEY)
    return FAKE_KEY


@pytest.fixture
def no_db(monkeypatch):
    """Keep tools off the network/DB: a fake connection, never a real one."""
    monkeypatch.setattr(tools, "_connect", _FakeConn)


def _rows(n: int = 40) -> list[dict]:
    return [
        {
            "id": i,
            "name": f"Место {i}",
            "category": "замок",
            "town": "Гродно",
            "district": None,
            "lat": 53.67 + i * 1e-4,
            "lon": 23.82,
        }
        for i in range(1, n + 1)
    ]


def _fake_model(payload: dict | None, tool_calls: list[tuple[str, dict]] | None = None):
    """A FunctionModel that calls `tool_calls` first, then emits `payload`.

    `seen_tools` records the tool names PydanticAI advertised to the model, so a
    test can pin the bounded surface.
    """
    seen_tools: list[set[str]] = []
    state = {"n": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        seen_tools.append({t.name for t in (info.function_tools or [])})
        state["n"] += 1
        if tool_calls and state["n"] <= len(tool_calls):
            name, args = tool_calls[state["n"] - 1]
            return ModelResponse([ToolCallPart(name, args)])
        return ModelResponse([ToolCallPart("final_result", json.dumps(payload or {}))])

    model = FunctionModel(fn, model_name="fake-interpret")
    return model, seen_tools


# (a) no key → None, deterministic fallback keeps the UI filters


def test_no_key_returns_none_and_ui_filters_survive(no_key):
    assert ai.available() is False

    req = GenerateReq(query=QUERY, hard_services=["туалет"], party_children=2)
    assert ai.interpret_with_agent(QUERY, req) is None

    # The degraded path the caller falls back to, with no model and no network.
    tr = build_requirements(QUERY, req)
    assert tr.source == "explicit"
    assert tr.party.children == 2
    assert "туалет" in tr.hard_service_codes()
    ui_reqs = [r for r in tr.of_kind("service") if r.code == "туалет"]
    assert len(ui_reqs) == 1 and ui_reqs[0].source == "ui" and ui_reqs[0].strength == "hard"
    assert tr.raw_query == QUERY


def test_missing_sdk_is_the_same_degradation(monkeypatch, fake_key):
    """No PydanticAI installed means no model — not an exception."""
    monkeypatch.setattr(ai, "pydantic_ai", None)
    assert ai.available() is False
    assert ai.interpret_with_agent(QUERY, GenerateReq(query=QUERY)) is None


def test_empty_query_returns_none(fake_key):
    assert ai.interpret_with_agent("   ", GenerateReq(query=QUERY)) is None


def test_model_failure_returns_none(fake_key, no_db, monkeypatch):
    def boom(messages, info: AgentInfo) -> ModelResponse:
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(ai, "_make_model", lambda: FunctionModel(boom, model_name="boom"))
    assert ai.interpret_with_agent(QUERY, GenerateReq(query=QUERY)) is None


def test_tool_call_budget_is_enforced(fake_key, no_db, monkeypatch):
    """A model that keeps calling tools hits the cap and yields None, not a hang."""
    monkeypatch.setattr(tools, "_db_search_rows", lambda db, *, query, limit, near: _rows(5))
    model, _ = _fake_model(
        None, tool_calls=[("search_places", {"query": "замок"})] * (ai.MAX_TOOL_CALLS + 3)
    )
    monkeypatch.setattr(ai, "_make_model", lambda: model)
    assert ai.interpret_with_agent(QUERY, GenerateReq(query=QUERY)) is None


def test_wall_clock_guard_times_out():
    result, failure = ai._run_with_timeout(lambda: time.sleep(0.3), 0.01)
    assert result is None and failure == "timeout"


def test_limits_are_bounded():
    """The point is that every ceiling is finite — a reading may not run away.

    The numbers themselves are a product decision and were raised after a
    measurement: a deep model needed 20-23 s per call, so the old 20 s per-request
    timeout was cutting its reasoning off mid-flight and the thin reading that came
    back looked like a weak model. Finite, but roomy enough to finish thinking.
    """
    assert 0 < ai.MAX_TOOL_CALLS <= 24
    assert 0 < ai.MAX_REQUESTS <= 12
    assert 0 < ai.MAX_OUTPUT_TOKENS <= 8192
    assert 0 < ai.WALL_CLOCK_TIMEOUT_S <= 120
    assert 0 < ai.MODEL_TIMEOUT_S < ai.WALL_CLOCK_TIMEOUT_S


def test_the_default_model_is_the_measured_one():
    """A deployment fact, deliberately pinned so a change is a deliberate act.

    The model is chosen by measurement (
    scored against a KNOWN-CORRECT answer per request), not by preference. The
    incumbent 2.5-pro was re-measured on 2026-09-30 and no longer beat
    2.5-flash — same 6/6 expectations met, four times the price and several
    times the latency — so the default moved. Changing this line without
    re-running that harness is how the deployment ends up on a model nobody
    measured.
    """
    assert ai._model_name(), "a model must always resolve"
    assert ai.DEFAULT_MODEL == "deepseek/deepseek-v4.1-flash"
    # The env override still wins, which is what the harness relies on.
    os.environ["AGENT_INTERPRET_MODEL"] = "example/override"
    try:
        assert ai._model_name() == "example/override"
    finally:
        del os.environ["AGENT_INTERPRET_MODEL"]


# (b) a fake model answer fills the contract


def _payload() -> dict:
    """What a well-behaved model would answer for QUERY — plus two traps:
    a `status` field it has no business setting, and a quote it invented."""
    return {
        "requirements": [
            {
                "kind": "service",
                "strength": "soft",
                "code": "туалет",
                "text": "туалет обязателен",
                "confidence": 0.7,
                "status": "satisfied",
            },
            {
                "kind": "service",
                "strength": "soft",
                "code": "кафе",
                "text": "кафе если по пути",
                "confidence": 0.6,
            },
            {
                "kind": "must_visit",
                "name": "Старый замок",
                "text": "старый Гродно",
                "confidence": 0.8,
            },
            {
                "kind": "service",
                "strength": "hard",
                "code": "вертолёт",
                "text": "если по пути",
                "confidence": 0.4,
            },
            {"kind": "interest", "code": "парк", "text": "я люблю парки", "confidence": 0.5},
            {"kind": "interest", "text": "хочу чего-нибудь эдакого", "confidence": 0.3},
        ],
        "children": 2,
        "children_ages": [5, 9],
        "budget_minutes": 120,
        "areas": ["grodno-old-town"],
        "unknowns": ["без лестниц"],
        "status": "ready",
        "note": "ignore me",
    }


@pytest.fixture
def fake_run(fake_key, no_db, monkeypatch):
    """Wire a fake model that calls search_places, then answers `_payload()`."""
    monkeypatch.setattr(tools, "_db_search_rows", lambda db, *, query, limit, near: _rows(20))
    model, seen = _fake_model(
        _payload(),
        tool_calls=[
            ("search_places", {"query": "замок", "category_codes": ["замок"], "limit": 999})
        ],
    )
    monkeypatch.setattr(ai, "_make_model", lambda: model)
    return seen


def test_fake_model_fills_the_contract(fake_run):
    req = GenerateReq(
        query=QUERY,
        locale="ru",
        party_children=2,
        hard_services=["туалет"],
        origin=None,
    )
    tr = ai.interpret_with_agent(QUERY, req)

    assert tr is not None
    # Explicit UI filters were merged in → "mixed", not "llm".
    assert tr.source == "mixed"
    assert tr.raw_query == QUERY
    assert tr.locale == "ru"

    # Hard service from the text, soft interest from the text.
    assert "кафе" in tr.soft_service_codes()
    assert [r.name for r in tr.of_kind("must_visit")] == ["Старый замок"]

    # UI precedence: the model said туалет is soft/whatever, the control said hard
    toilets = [r for r in tr.of_kind("service") if r.code == "туалет"]
    assert len(toilets) == 1
    assert toilets[0].source == "ui" and toilets[0].strength == "hard"

    # Provenance: the verbatim fragment, and only a real one
    cafe = next(r for r in tr.requirements if r.code == "кафе")
    assert cafe.source == "text" and cafe.text == "кафе если по пути"
    assert cafe.text in QUERY
    parks = next(r for r in tr.requirements if r.kind == "interest")
    assert parks.code == "парк"
    assert parks.text is None, "an invented quote must be dropped, not stored"

    # Party / budget / areas
    assert tr.party.children == 2
    assert tr.party.children_ages == [5, 9]
    assert tr.budget_minutes == 120
    assert tr.areas == ["grodno-old-town"]

    # Unsupported asks are named, never dropped silently
    assert "без лестниц" in tr.unknowns
    assert "unknown_category:вертолёт" in tr.unknowns
    assert any(u.startswith("unknown_category:") and "эдакого" in u for u in tr.unknowns), (
        "a requirement with no canonical code becomes an unknown, never a guess"
    )

    # The bounded tool surface the model was offered
    assert fake_run, "the model was never asked anything"
    assert fake_run[0] == BOUNDED_TOOLS


def test_agent_reading_has_no_status_field(fake_run):
    """Satisfaction is structurally unrepresentable in the agent's output."""
    assert "status" not in ai.AgentReading.model_fields
    assert "status" not in ai.AgentRequirement.model_fields
    assert "place_ids" not in ai.AgentRequirement.model_fields


def test_agent_never_marks_a_requirement_satisfied(fake_run):
    tr = ai.interpret_with_agent(QUERY, GenerateReq(query=QUERY, hard_services=["туалет"]))
    assert tr is not None
    assert tr.requirements, "the contract must not come back empty"
    assert all(r.status == "pending" for r in tr.requirements)
    assert not tr.is_ready()
    assert tr.unresolved_hard()  # the verifier, not the agent, resolves these


def test_invented_place_id_is_rejected(fake_key, no_db, monkeypatch):
    monkeypatch.setattr(tools, "_db_search_rows", lambda db, *, query, limit, near: _rows(3))
    payload = {
        "requirements": [
            {"kind": "must_visit", "name": "Старый замок", "place_id": 2, "confidence": 0.9},
            {
                "kind": "must_visit",
                "name": "Выдуманное место",
                "place_id": 999999,
                "confidence": 0.9,
            },
        ]
    }
    model, _ = _fake_model(payload, tool_calls=[("search_places", {"query": "замок"})])
    monkeypatch.setattr(ai, "_make_model", lambda: model)

    tr = ai.interpret_with_agent("замок", GenerateReq(query="замок"))
    assert tr is not None
    by_name = {r.name: r for r in tr.of_kind("must_visit")}
    assert by_name["Старый замок"].place_id == 2  # returned by the tool this run
    assert by_name["Выдуманное место"].place_id is None  # hallucinated id dropped


def test_children_ages_are_never_invented(fake_key, no_db, monkeypatch):
    payload = {"children": 2, "children_ages": [4, 7], "requirements": []}
    model, _ = _fake_model(payload)
    monkeypatch.setattr(ai, "_make_model", lambda: model)

    query = "двое детей, погулять по Гродно"
    tr = ai.interpret_with_agent(query, GenerateReq(query=query))
    assert tr is not None
    assert tr.party.children == 2
    assert tr.party.children_ages == [], "ages not named in the text must stay absent"


def test_ui_only_filters_still_mark_the_source_mixed(fake_key, no_db, monkeypatch):
    model, _ = _fake_model({"requirements": []})
    monkeypatch.setattr(ai, "_make_model", lambda: model)
    req = GenerateReq(query="погулять по Гродно", party_children=1)
    tr = ai.interpret_with_agent("погулять по Гродно", req)
    assert tr is not None and tr.source == "mixed"
    assert tr.party.children == 1


def test_llm_only_reading_is_tagged_llm(fake_key, no_db, monkeypatch):
    model, _ = _fake_model({"requirements": [{"kind": "interest", "code": "замок"}]})
    monkeypatch.setattr(ai, "_make_model", lambda: model)
    tr = ai.interpret_with_agent("замки", GenerateReq(query="замки"))
    assert tr is not None and tr.source == "llm"
    assert tr.interest_codes() == ["замок"]


# (b2) what the trace says about the call to the model
# A reader of a trace can see the reading the pipeline got; only the prompt says
# *why* it got it. These pin the prompt, the tokens and the failure case.


def _model_spans(trace_id: str) -> list:
    tracked = trace._traces.get(trace_id)
    return [s for s in tracked.spans if s.kind == "generation"] if tracked else []


def test_the_model_call_carries_the_prompt_it_was_given(fake_run):
    trace.begin("job-model")
    try:
        tr = ai.interpret_with_agent(QUERY, GenerateReq(query=QUERY, locale="ru"))
        call, = _model_spans("job-model")
    finally:
        trace.finish()

    assert tr is not None
    assert call.name == "interpret · model"
    assert call.model == ai.DEFAULT_MODEL
    system, user = call.input
    assert system["role"] == "system" and "Grodno region" in system["content"]
    assert user["role"] == "user"
    # The request as the model saw it, not only the reading that came back.
    assert f"request={QUERY!r}" in user["content"]
    assert call.output["children"] == 2  # the answer, as the model returned it
    assert set(call.usage) == {"input", "output", "total"}
    assert call.usage["total"] == call.usage["input"] + call.usage["output"]
    assert call.facts["requests"] >= 1


def test_an_answer_that_is_not_a_reading_is_recorded_as_an_error():
    """«The agent fell back to the deterministic path» must be explainable."""

    class Response:
        text = "не JSON вовсе"

    class Result:
        output = "не JSON вовсе"
        usage = None
        response = Response()

    trace.begin("job-bad-answer")
    try:
        ai._record_model_call(GenerateReq(query=QUERY), "request='...'", Result())
        call, = _model_spans("job-bad-answer")
    finally:
        trace.finish()

    assert call.status == "error"
    # What it actually said, so the failure is readable rather than merely known.
    assert call.output == "не JSON вовсе"
    # No usage was reported, so there is nothing to say about tokens — but the
    # call did reach the model, and saying so is the point of the flag: the
    # cache-hit span carries `cached=True`, and without this one a reader could
    # not tell the two apart.
    assert call.usage is None and call.facts == {"cached": False}


# (c) tool caps, and tools never raise at the agent


def test_search_places_caps_results(monkeypatch, no_db):
    monkeypatch.setattr(tools, "_db_search_rows", lambda db, *, query, limit, near: _rows(40))

    out = tools.search_places("замки", category_codes=["замок"], limit=999)
    assert out["error"] is None
    assert out["count"] == tools.MAX_PLACES_PER_SEARCH
    assert out["capped"] is True
    assert out["provenance"]["result_cap"] == tools.MAX_PLACES_PER_SEARCH
    assert all(set(row) <= set(tools._PLACE_FIELDS) for row in out["results"])

    # A modest limit is honoured and not reported as capped.
    small = tools.search_places("замки", category_codes=["замок"], limit=3)
    assert small["count"] == 3 and small["capped"] is False

    # Unknown category codes are dropped, never sent to SQL or guessed.
    mixed = tools.search_places("замки", category_codes=["замок", "вертолёт"], limit=2)
    assert mixed["provenance"]["categories"] == ["замок"]
    assert mixed["provenance"]["dropped_codes"] == ["вертолёт"]


def test_search_places_clamps_radius(monkeypatch, no_db):
    monkeypatch.setattr(tools, "_db_search_rows", lambda db, *, query, limit, near: _rows(2))
    out = tools.search_places("замки", near_lat=53.67, near_lon=23.82, radius_m=10_000_000)
    assert out["provenance"]["strategy"] == "nearby"
    assert out["provenance"]["radius_m"] == tools.MAX_RADIUS_M
    assert out["capped"] is True


def test_tools_never_raise_raw_errors(monkeypatch, no_db):
    def boom(*args, **kwargs):
        raise RuntimeError("db on fire")

    monkeypatch.setattr(tools, "_db_search_rows", boom)
    monkeypatch.setattr(tools, "_db_area_rows", boom)
    monkeypatch.setattr(tools, "_db_place_row", boom)
    # Force the DB fallback for areas so the DB error path is the one under test.
    monkeypatch.setattr(
        tools,
        "_areas_from_registry",
        lambda term, locale, limit: (_ for _ in ()).throw(ImportError("no agent.areas")),
    )

    search = tools.search_places("замки")
    assert search["error"] == tools.ERR_DB_UNAVAILABLE and search["results"] == []
    assert tools.find_areas("старый город")["error"] == tools.ERR_DB_UNAVAILABLE
    assert tools.get_place_facts(1)["error"] == tools.ERR_DB_UNAVAILABLE

    # Bad arguments are answered, not raised.
    assert tools.search_places("  ")["error"] == tools.ERR_EMPTY_QUERY
    assert tools.find_areas("")["error"] == tools.ERR_BAD_ARGUMENT
    assert tools.get_place_facts(-3)["error"] == tools.ERR_BAD_ARGUMENT

    # A dead driver is a deployment fact: still an envelope.
    def no_driver():
        raise ImportError("no psycopg")

    monkeypatch.setattr(tools, "_connect", no_driver)
    assert tools.search_places("замки")["error"] == tools.ERR_DB_UNAVAILABLE


def test_get_place_facts_returns_raw_facts_with_provenance(monkeypatch, no_db):
    row = {
        "id": 7,
        "name": "Старый замок",
        "category": "замок",
        "town": "Гродно",
        "district": None,
        "lat": 53.67,
        "lon": 23.82,
        "opening_hours": "Mo-Su 10:00-18:00",
        "ticket_price": "10 BYN",
        "visit_minutes": 60,
        "source_url": "https://example.invalid/place/7",
        "links": [
            {"title": "a", "url": "x"},
            {"title": "b", "url": "y"},
            {"title": "c", "url": "z"},
            {"title": "d", "url": "w"},
        ],
    }
    monkeypatch.setattr(tools, "_db_place_row", lambda db, place_id: row)

    out = tools.get_place_facts(7)
    assert out["error"] is None and out["count"] == 1
    facts = out["results"][0]
    assert facts["opening_hours"] == "Mo-Su 10:00-18:00"
    assert facts["ticket_price"] == "10 BYN"
    assert facts["source_url"].endswith("/7")
    assert len(facts["links"]) == tools.MAX_LINKS_PER_PLACE
    assert out["provenance"]["fact_status"] == "raw_unverified"
    assert out["provenance"]["result_cap"] == tools.MAX_FACTS_PER_CALL

    # Missing place → honest not_found, not an empty success.
    monkeypatch.setattr(tools, "_db_place_row", lambda db, place_id: None)
    assert tools.get_place_facts(4242)["error"] == tools.ERR_NOT_FOUND


def test_find_areas_resolves_through_the_registry(monkeypatch, no_db):
    """The versioned registry is the primary source; the real match logic runs."""
    registry = {
        f"area-{i}": {
            "name_ru": f"Район {i}",
            "name_en": None,
            "kind": "district",
            "aliases": {"ru": [], "en": []},
        }
        for i in range(30)
    }
    registry["grodno-old-town"] = {
        "name_ru": "Старый город",
        "name_en": "Old Town",
        "kind": "project",
        "aliases": {"ru": ["Старогородка"], "en": []},
    }
    monkeypatch.setattr(areas_mod, "load_areas", lambda: registry)
    monkeypatch.setattr(
        areas_mod,
        "resolve_area",
        lambda term, locale=None: "grodno-old-town" if "стар" in term.lower() else None,
    )

    out = tools.find_areas("старый город")
    assert out["error"] is None
    assert out["results"][0]["code"] == "grodno-old-town"
    assert out["provenance"]["source"] == "areas.json"

    # An alias resolves too, and an unknown term is an empty success — never a guess.
    assert tools.find_areas("Старогородка")["results"][0]["code"] == "grodno-old-town"
    assert tools.find_areas("Вильнюс")["results"] == []
    assert tools.find_areas("Вильнюс")["error"] is None

    # Match-all term → capped, reported.
    capped = tools.find_areas("район")
    assert capped["count"] == tools.MAX_AREAS_PER_CALL
    assert capped["capped"] is True
    assert set(capped["results"][0]) == set(tools._AREA_FIELDS)


def test_find_areas_falls_back_to_the_db_without_the_registry(monkeypatch, no_db):
    def no_registry(term, locale, limit):
        raise ImportError("agent.areas missing")

    rows = [
        {"code": f"district:{i}", "name_ru": f"Район {i}", "name_en": None, "kind": "district"}
        for i in range(30)
    ]
    monkeypatch.setattr(tools, "_areas_from_registry", no_registry)
    monkeypatch.setattr(tools, "_db_area_rows", lambda db, term, limit: rows)

    out = tools.find_areas("район")
    assert out["error"] is None
    assert out["count"] == tools.MAX_AREAS_PER_CALL and out["capped"] is True
    assert out["provenance"]["source"] == "areas (db fallback)"


def test_broken_area_registry_is_an_error_not_an_exception(monkeypatch, no_db):
    def broken(term, locale, limit):
        raise ValueError("areas.json: duplicate slug")

    monkeypatch.setattr(tools, "_areas_from_registry", broken)
    out = tools.find_areas("Гродно")
    assert out["error"] == tools.ERR_AREA_REGISTRY
    assert out["results"] == []


def test_services_near_route_is_an_honest_gap(no_db):
    out = tools.services_near_route(
        ["туалет", "вертолёт"], shape=[[53.67, 23.82]] * 100, max_detour_minutes=999
    )
    assert out["error"] == tools.ERR_NOT_IMPLEMENTED
    assert out["results"] == [] and out["count"] == 0
    assert out["capped"] is True  # shape + detour both clamped
    prov = out["provenance"]
    assert prov["dropped_codes"] == ["вертолёт"]
    assert prov["points_in"] == 100
    assert prov["max_detour_minutes"] == tools.MAX_DETOUR_MINUTES
    assert "not implemented" in out["message"]


def test_services_near_route_reaches_the_agent_without_raising(fake_key, no_db, monkeypatch):
    """The agent may call the gap tool; the answer must not blow up the run."""
    payload = {
        "requirements": [
            {
                "kind": "service",
                "strength": "hard",
                "code": "туалет",
                "text": "туалет обязателен",
                "confidence": 0.8,
            }
        ],
        "unknowns": ["туалет на маршруте не подтверждён"],
    }
    model, seen = _fake_model(
        payload,
        tool_calls=[
            ("services_near_route", {"category_codes": ["туалет"], "shape": [[53.7, 23.8]]})
        ],
    )
    monkeypatch.setattr(ai, "_make_model", lambda: model)

    tr = ai.interpret_with_agent(QUERY, GenerateReq(query=QUERY))
    assert tr is not None and seen
    # Unproven service stays hard+pending and is surfaced as an unknown.
    assert "туалет" in tr.hard_service_codes()
    assert all(r.status == "pending" for r in tr.requirements)
    assert any("не подтверждён" in u for u in tr.unknowns)

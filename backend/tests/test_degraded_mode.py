"""Degraded mode: no OPENROUTER_API_KEY (or a model/upstream that is down)
must never turn a route request into a 500.

The bug this file used to pin down, reproduced on a machine with no key: the
old model-backed intent step raised on every request and the planner answered
HTTP 500 for every POST /routes/generate.

What must hold, per step:
  * intent — the deterministic reader answers: categories from the shared
    keyword→category map, an explicit duration kept, named places still
    extracted.  It is a first-class mode, not an anomaly: no warning storm.
  * the interpretation agent — when it fails (or has no key) `build_requirements`
    silently takes the deterministic contract; when it answers, its contract
    drives the reading the planner uses.
  * embed   — no vector, retrieval runs keyword/category-only.
  * health  — still reports llm/embedder false, so the flags stay honest.
  * HTTP    — an upstream error that escapes the planner is a 503 with a
    detail, never an opaque 500.

No network except the last class, which uses the live DB + Valhalla and
skips when they are not reachable.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import constants, main as agent_main
from agent.config import openrouter_api_key, settings
from agent.errors import NoCandidatesFound, NoRoutePossible, UpstreamUnavailable
from agent.models import Candidate, GenerateReq, ResolvedConstraints
from agent.planner import (
    agent_interpret as ai,
    intent as intent_mod,
    pipeline as pipeline_mod,
    retrieve as retrieve_mod,
)
from agent.planner.intent import build_requirements, extract_intent, fallback_intent
from agent.planner.pipeline import Pipeline, _openrouter_embed
from agent.requirements import PartyComposition, Requirement, TripRequirements
from agent.valhalla_client import ping as valhalla_ping

QUERY = "Хочу погулять по замкам Гродно"
DSN = "postgresql://grodno:grodno@localhost:5432/grodno"


# ── Fixtures: the key is the switch between degraded and full mode ───────────

@pytest.fixture
def no_key(monkeypatch):
    """A process without an OpenRouter key: the env var AND the import-time
    settings snapshot are cleared — otherwise a key from the developer's
    shell leaks in and the test would exercise the wrong branch."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None, raising=False)
    assert openrouter_api_key() is None


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-test-key", raising=False)
    assert openrouter_api_key() is not None


@contextmanager
def offline() -> Iterator[None]:
    """Belt and braces: nothing in this block may reach OpenRouter, whatever
    the environment says."""
    old_env = os.environ.pop("OPENROUTER_API_KEY", None)
    old_setting = settings.OPENROUTER_API_KEY
    settings.OPENROUTER_API_KEY = None
    try:
        yield
    finally:
        if old_env is not None:
            os.environ["OPENROUTER_API_KEY"] = old_env
        settings.OPENROUTER_API_KEY = old_setting


def _c(id: int, relevance: float = 0.5, name: str = "place") -> Candidate:
    return Candidate(id=id, name=name, category="замок", lat=53.68, lon=23.82,
                     relevance=relevance, rrf_score=relevance)


def _agent_contract(*, source: str = "llm", codes: tuple[str, ...] = ()) -> TripRequirements:
    """A contract shaped exactly as the interpretation agent returns one."""
    return TripRequirements(
        locale="ru",
        raw_query=QUERY,
        party=PartyComposition(adults=1),
        requirements=[
            Requirement(kind="interest", strength="soft", code=c, label=c) for c in codes
        ],
        source=source,  # type: ignore[arg-type]
    )


def _explode(*_a, **_kw):
    raise AssertionError("OpenRouter must not be called in degraded mode")


def _warnings(caplog, logger: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger and r.levelname == "WARNING"]


class _Resp:
    """Stand-in for an httpx response."""

    def __init__(self, status: int = 200, body: dict | None = None):
        self._status = status
        self._body = body or {"answers": {}}

    def raise_for_status(self):
        if self._status >= 400:
            raise httpx.HTTPStatusError(
                f"status {self._status}",
                request=httpx.Request("POST", "https://openrouter.ai"),
                response=httpx.Response(self._status),
            )

    def json(self) -> dict:
        return self._body


def _fake_post_client(response: _Resp):
    """An httpx.Client whose post() answers with `response`."""

    class _Client:
        def __init__(self, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def post(self, *_a, **_kw):
            return response

    return _Client


def _boom_client(exc: BaseException):
    class _Client:
        def __init__(self, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def post(self, *_a, **_kw):
            raise exc

    return _Client


# ─────────────────────────────────────────────────────────────────────────────
# The interpretation agent — no key means no model, and that is not an error
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentAvailability:

    def test_no_key_means_unavailable(self, no_key):
        assert ai.available() is False

    def test_key_present_means_available(self, with_key):
        assert ai.available() is True

    def test_no_key_returns_no_contract(self, no_key, monkeypatch):
        monkeypatch.setattr(ai, "_run_agent", _explode)
        assert ai.interpret_with_agent(QUERY, GenerateReq(query=QUERY)) is None


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — intent degrades to a deterministic parse of the same text
# ─────────────────────────────────────────────────────────────────────────────

class TestIntentFallback:
    """Step 1 with no model: the deterministic reader, and the agent hand-off."""

    def test_no_key_never_calls_openrouter(self, no_key, monkeypatch):
        monkeypatch.setattr(ai, "_run_agent", _explode)
        res = extract_intent(QUERY)          # used to raise → HTTP 500
        assert res.source == "regex"
        assert res.raw_response is None
        assert res.confidence == 0.0
        assert res.decision.intent_type in constants.INTENT_TYPES

    def test_no_key_categories_come_from_the_shared_map(self, no_key):
        """The reader fills categories from the deterministic keyword→category
        map (resolve.CATEGORY_SYNONYMS) — the same taxonomy retrieval uses —
        not from a model guess.  «замкам» → замок, so "замки Гродно" still
        retrieves castles in degraded mode.  Exclusion ("без замков") and
        keywords stay a no-invent zone: the maps do not carry them."""
        d = extract_intent(QUERY).decision
        assert "замок" in d.categories_pos
        assert d.categories_neg == []
        assert d.keywords_pos == []
        assert d.keywords_neg == []

    def test_no_key_keeps_the_named_places(self, no_key):
        d = fallback_intent(QUERY).decision
        assert "Гродно" in d.named_places      # resolves through the DB path
        # Region names are still filtered out (same stop-list as the agent path).
        region = fallback_intent("достопримечательности Гродненской области").decision
        assert region.named_places == []

    @pytest.mark.parametrize(
        "query,minutes",
        [
            ("погулять за 3 часа", 180),
            ("погулять 2 часа по костёлам", 120),
            ("на 90 минут по замкам", 90),
            ("на полдня", 240),
            ("на весь день", 480),
            ("просто погулять", None),
            ("хочу в воскресенье", None),        # a bare "день" is not a budget
            ("погулять без ограничения", None),
        ],
    )
    def test_time_budget_only_when_the_query_states_one(self, no_key, query, minutes):
        assert fallback_intent(query).decision.time_budget_minutes == minutes

    @pytest.mark.parametrize(
        "query,scope",
        [
            ("все костёлы Гродненской области", "region"),
            ("что посмотреть по всей области", "region"),
            ("замки Гродно", "town"),
            ("костёлы Лидского района", "district"),
        ],
    )
    def test_scope_read_off_the_text(self, no_key, query, scope):
        assert fallback_intent(query).decision.search_scope == scope

    def test_no_key_is_quiet_on_the_intent_path(self, no_key, caplog):
        """The deterministic reader is a first-class mode, not an anomaly: it
        must not emit a warning for every call (the old degraded path did)."""
        with caplog.at_level("WARNING", logger="agent.planner.intent"):
            build_requirements(QUERY, GenerateReq(query=QUERY))
        assert _warnings(caplog, "agent.planner.intent") == []

    def test_agent_failure_degrades_to_the_deterministic_contract(
        self, with_key, monkeypatch, caplog
    ):
        def boom(*_a, **_kw):
            raise RuntimeError("agent: upstream failed: 502")
        monkeypatch.setattr(intent_mod, "_agent_contract", boom)
        with caplog.at_level("WARNING", logger="agent.planner.intent"):
            tr = build_requirements(QUERY, GenerateReq(query=QUERY))  # must not propagate
        assert tr.source == "fallback"
        # The degraded reader must still understand the ask itself…
        assert "замок" in [r.code for r in tr.requirements if r.kind == "interest"]
        # …but «Гродно» in the query is the city we are walking in, not a stop.
        # This used to assert a mandatory place named «Гродно»: nothing in the
        # data carries that name, so the deterministic verifier could only report
        # it `unmet` — in nearly every answer. That phantom is what the stage
        # evals caught (evals/cases/interpretation.jsonl), so the assertion now
        # pins its absence rather than its presence.
        assert tr.must_visit_names() == []
        assert len(_warnings(caplog, "agent.planner.intent")) == 1

    def test_agent_contract_drives_the_reading(self, with_key, monkeypatch):
        """With the agent answering, the IntentResult the planner consumes comes
        from the contract — and the facts the text states are still there (the
        model may add meaning, never lose a fact the parser found)."""
        monkeypatch.setattr(
            intent_mod, "_agent_contract",
            lambda *a, **k: _agent_contract(codes=("костёл",)),
        )
        tr = build_requirements(QUERY, GenerateReq(query=QUERY))
        assert tr.source == "llm"
        intent = intent_mod.intent_from_requirements(tr, QUERY)
        assert intent.source == "agent"
        assert "костёл" in intent.decision.categories_pos   # the model's reading
        assert "замок" in intent.decision.categories_pos    # the text's own fact

    def test_source_stays_inside_the_model_literal(self):
        assert fallback_intent(QUERY).source == "regex"
        assert intent_mod._fallback_time_budget("2 часа") == 120


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# Embeddings — keyword-only retrieval, never an exception
# ─────────────────────────────────────────────────────────────────────────────

class TestEmbedDegraded:

    def test_no_key_returns_no_vector(self, no_key, monkeypatch):
        monkeypatch.setattr(httpx, "Client", _explode)
        assert _openrouter_embed([QUERY]) == []

    def test_upstream_failure_returns_no_vector(self, with_key, monkeypatch):
        monkeypatch.setattr(httpx, "Client", _boom_client(httpx.ConnectError("no route to host")))
        assert _openrouter_embed([QUERY]) == []

    def test_malformed_embedding_response_returns_no_vector(self, with_key, monkeypatch):
        monkeypatch.setattr(httpx, "Client", _fake_post_client(_Resp(body={"data": "nope"})))
        assert _openrouter_embed([QUERY]) == []

    def test_logs_one_warning_naming_the_reason(self, no_key, monkeypatch, caplog):
        monkeypatch.setattr(httpx, "Client", _explode)
        with caplog.at_level("WARNING", logger="agent.planner.pipeline"):
            _openrouter_embed([QUERY])
        warnings = _warnings(caplog, "agent.planner.pipeline")
        assert len(warnings) == 1
        assert "OPENROUTER_API_KEY" in warnings[0]

    def test_a_good_response_is_used(self, with_key, monkeypatch):
        body = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
        monkeypatch.setattr(httpx, "Client", _fake_post_client(_Resp(body=body)))
        assert _openrouter_embed([QUERY]) == [[0.1, 0.2, 0.3]]


# ─────────────────────────────────────────────────────────────────────────────
# HTTP — the last-resort net, and the health flags
# ─────────────────────────────────────────────────────────────────────────────

class _StubPlanner:
    """A planner whose every entry point raises a preset error."""

    def __init__(self, exc: BaseException | None = None):
        self._exc = exc

    def _raise(self, *_a, **_kw):
        if self._exc is not None:
            raise self._exc
        raise AssertionError("the stub should have raised")

    generate = _raise
    reroute = _raise
    explain_route = _raise
    health = _raise


class _FakeDB:
    """Just enough connection for Pipeline.health()'s `SELECT 1`."""

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, _sql):
        return None

    def fetchone(self):
        return (1,)


def _client_with_planner(planner) -> TestClient:
    """TestClient without the lifespan → no DB connection is made."""
    agent_main.app.state.planner = planner
    return TestClient(agent_main.app, raise_server_exceptions=False)


class _HealthOnly:
    def __init__(self, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "valhalla_ping", lambda: True, raising=False)

    def health(self) -> dict:
        return Pipeline(db=_FakeDB()).health()  # type: ignore[arg-type]


@pytest.fixture
def _restore_planner():
    yield
    if hasattr(agent_main.app.state, "planner"):
        delattr(agent_main.app.state, "planner")


class TestHttpDegraded:

    def test_escaped_upstream_error_is_503_not_500(self, _restore_planner):
        client = _client_with_planner(
            _StubPlanner(UpstreamUnavailable("valhalla: /route failed: 502"))
        )
        r = client.post("/routes/generate", json={"query": QUERY})
        assert r.status_code == 503
        assert "502" in r.json()["detail"]

    def test_a_degraded_upstream_is_never_a_bare_500(self, _restore_planner):
        client = _client_with_planner(_StubPlanner(UpstreamUnavailable("db: gone")))
        assert client.post("/routes/generate", json={"query": QUERY}).status_code == 503

    def test_planner_errors_keep_their_own_status(self, _restore_planner):
        client = _client_with_planner(
            _StubPlanner(NoCandidatesFound("no candidates matched the query"))
        )
        r = client.post("/routes/generate", json={"query": QUERY})
        assert r.status_code == 404
        assert "no candidates" in r.json()["detail"]

    def test_health_flags_are_honest_without_a_key(self, no_key, monkeypatch, _restore_planner):
        _HealthOnly(monkeypatch)
        body = _client_with_planner(_HealthOnly(monkeypatch)).get("/health").json()
        assert body["embedder"] is False
        assert body["llm"] is False
        assert body["db"] is True
        assert body["valhalla"] is True
        # Degraded, and honest about which part is degraded.
        assert body["status"] == "degraded"

    def test_health_flags_are_honest_with_a_key(self, with_key, monkeypatch, _restore_planner):
        _HealthOnly(monkeypatch)
        body = _client_with_planner(_HealthOnly(monkeypatch)).get("/health").json()
        assert body["llm"] is True
        assert body["embedder"] is True
        assert body["status"] == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# The real thing: a route request with no key, against the live DB + Valhalla
# ─────────────────────────────────────────────────────────────────────────────

def _db_up() -> bool:
    try:
        psycopg.connect(DSN, connect_timeout=3).close()
    except Exception:
        return False
    return True


def _valhalla_up() -> bool:
    try:
        return bool(valhalla_ping())
    except Exception:
        return False


@pytest.fixture(scope="module")
def live_db() -> Iterator[psycopg.Connection]:
    if not _db_up():
        pytest.skip(f"live DB not reachable at {DSN}")
    if not _valhalla_up():
        pytest.skip("Valhalla not reachable")
    db = psycopg.connect(DSN, autocommit=True)
    with db.cursor() as cur:
        cur.execute("SET pg_trgm.word_similarity_threshold = 0.45")
    yield db
    db.close()


class TestLiveDegradedRoute:
    """The deliverable: no key ⇒ POST /routes/generate still returns points.

    This is the exact request that answered HTTP 500 before the fix.
    """

    def test_route_request_without_a_key_returns_points(self, no_key, live_db):
        with offline():
            resp = Pipeline(db=live_db).generate(
                GenerateReq(query=QUERY, time_budget_minutes=120)
            )
        assert resp.debug["intent_source"] == "fallback"
        assert len(resp.points) >= 2, "a route needs at least two stops"
        assert all(p.lat and p.lon for p in resp.points)
        assert resp.explanation
        # The verifier's verdict travels with the response, and the
        # requirements the (deterministic) reading produced are visible.
        assert resp.status in ("ready", "degraded", "needs_clarification")
        assert resp.requirements

    def test_a_stated_duration_reaches_the_constraints(self, no_key, live_db):
        with offline():
            resp = Pipeline(db=live_db).generate(
                GenerateReq(query="погулять по замкам Гродно 3 часа", time_budget_minutes=None)
            )
        assert resp.debug["constraints"]["time_budget_minutes"] == 180
        assert len(resp.points) >= 2

    def test_the_vector_signal_is_skipped(self, no_key, live_db, monkeypatch):
        """An empty query_embedding is what switches the vector signal off."""
        calls: list = []

        def _record(*args, **kwargs):
            calls.append(args)
            return []

        monkeypatch.setattr(retrieve_mod, "_vector_signal", _record)
        constraints = ResolvedConstraints(
            must_visit_ids=[], area_anchor=None, optional_categories=[],
            forbidden_categories=[], forbidden_keywords=[], time_budget_minutes=120,
            bbox=None, era_hint="any", party_type="solo", intent_type="discovery",
            must_visit_keywords=[], query_keywords=[],
        )
        pool = retrieve_mod.retrieve(constraints, [], live_db, query_text=QUERY)
        assert calls == []
        assert len(pool) > 0, "keyword + category signals alone must still find places"


def test_a_route_that_cannot_be_planned_is_an_answer_not_a_failed_request():
    """«Cannot plan here» must not leave as HTTP 422 with the optimizer's sentence.

    The optimizer guard (`raise NoRoutePossible`) used to reach the client as
    `422 {"detail": "optimizer could not produce a route with ≥ 2 stops"}`: an
    English implementation detail, no plan, no reason code, nothing for the UI to
    render — and a request the walk could not serve died outright, while the same
    question asked with one extra clause answered 200. Measured live: «Старый
    Гродно, два часа, туалет обязателен» → 422, while the same request with
    «двое детей 6 и 9 лет … без музеев» → 200 and a three-stop plan. The coverage
    gate already answers this class of «no» with an ordinary response, so this
    pins the same shape for the optimizer's refusal.
    """

    class _Refusing:
        def generate(self, req=None, **kwargs):
            raise NoRoutePossible("optimizer could not produce a route with ≥ 2 stops")

    client = _client_with_planner(_Refusing())
    r = client.post(
        "/routes/generate",
        json={"query": "Старый Гродно, два часа, туалет обязателен"},
    )

    assert r.status_code == 200, "a refusal to plan is an answer, not a bad request"
    body = r.json()
    assert body["status"] == "infeasible"
    assert body["points"] == [], "nothing was planned, so nothing is claimed"
    assert body["shape"] == {}
    assert body["result_mode"] == "route"
    assert body["debug"]["reason"] == "no_walkable_route", "the reason stays machine-readable"
    # The optimizer's own sentence is evidence, not the tourist's text.
    assert "optimizer" not in (body.get("explanation") or "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

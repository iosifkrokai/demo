"""No interpreter configured: the request is refused, not guessed at.

Reading a free-text query is the model's job. Without a key there is no reading,
so the planning endpoints answer 503 ``llm_not_configured`` and ``/health`` says
so plainly. Nothing downstream pretends to have understood the query, and the
catalogue endpoints (which need no reading) keep answering.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import client as ai, runner as ai_runner
from api import main as agent_main
from contracts.planner import Candidate, GenerateReq, ResolvedConstraints
from core.config import openrouter_api_key, settings
from core.errors import (
    InterpretationUnavailable,
    NoCandidatesFound,
    NoRoutePossible,
    UpstreamUnavailable,
)
from domain.requirements import PartyComposition, Requirement, TripRequirements
from ml import embeddings
from planner import (
    intent as intent_mod,
    pipeline as pipeline_mod,
    retrieve as retrieve_mod,
)
from planner.intent import build_requirements
from planner.pipeline import Pipeline, _embed_query
from planner.valhalla_client import ping as valhalla_ping

QUERY = "Хочу погулять по замкам Гродно"
DSN = "postgresql://grodno:grodno@localhost:5432/grodno"


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
    raise AssertionError("OpenRouter must not be called without a key")


def _warnings(caplog, logger: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger and r.levelname == "WARNING"]


class _FakeModel:
    """A stand-in for the local fastembed model (no download, no CPU)."""

    def __init__(self, *, fail: bool = False, vectors: list[list[float]] | None = None):
        self._fail = fail
        self._vectors = vectors
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        if self._fail:
            raise RuntimeError("model not baked into this image")
        if self._vectors is not None:
            return self._vectors
        return [[0.1, 0.2, 0.3] for _ in texts]


@pytest.fixture
def fake_model(monkeypatch):
    """Install a fake local model; tests must never load the real one."""
    from agent import interpret_cache

    interpret_cache.EMBED_CACHE.clear()
    model = _FakeModel()
    monkeypatch.setattr(embeddings._state, "model", model, raising=False)
    monkeypatch.setattr(embeddings._state, "available", None, raising=False)
    yield model
    interpret_cache.EMBED_CACHE.clear()


class TestAgentAvailability:

    def test_no_key_means_unavailable(self, no_key):
        assert ai.available() is False

    def test_key_present_means_available(self, with_key):
        assert ai.available() is True

    def test_no_key_returns_no_contract(self, no_key, monkeypatch):
        monkeypatch.setattr(ai_runner, "_run_agent", _explode)
        assert ai.interpret_with_agent(QUERY, GenerateReq(query=QUERY)) is None


class TestNoReaderRefuses:
    """No reading means no plan — never a keyword guess dressed up as one."""

    def test_no_key_never_calls_the_model(self, no_key, monkeypatch):
        monkeypatch.setattr(ai_runner, "_run_agent", _explode)
        with pytest.raises(InterpretationUnavailable):
            build_requirements(QUERY, GenerateReq(query=QUERY))

    def test_a_failed_reading_is_not_replaced_by_a_guess(self, with_key, monkeypatch):
        def boom(*_a, **_kw):
            raise RuntimeError("agent: upstream failed: 502")
        monkeypatch.setattr(ai, "interpret_with_agent", boom)
        with pytest.raises(InterpretationUnavailable):
            build_requirements(QUERY, GenerateReq(query=QUERY))

    def test_the_agent_contract_drives_the_reading(self, with_key, monkeypatch):
        """With the agent answering, the IntentResult comes from the contract."""
        monkeypatch.setattr(
            intent_mod, "_agent_contract",
            lambda *a, **k: _agent_contract(codes=("костёл",)),
        )
        tr = build_requirements(QUERY, GenerateReq(query=QUERY))
        assert tr.source == "llm"
        intent = intent_mod.intent_from_requirements(tr, QUERY)
        assert intent.source == "agent"
        assert "костёл" in intent.decision.categories_pos

    def test_an_explicit_ui_choice_survives_the_reading(self, with_key, monkeypatch):
        """The model reads the text; the control the tourist pressed must not be lost."""
        monkeypatch.setattr(
            intent_mod, "_agent_contract", lambda *a, **k: _agent_contract()
        )
        tr = build_requirements(
            QUERY, GenerateReq(query=QUERY, hard_services=["туалет"])
        )
        assert "туалет" in tr.hard_service_codes()
        assert tr.source == "mixed"


class TestEmbedLocal:

    def test_local_vector_is_used(self, fake_model):
        assert _embed_query(QUERY) == [0.1, 0.2, 0.3]

    def test_query_gets_the_query_prefix(self, fake_model):
        _embed_query(QUERY)
        assert fake_model.calls and all(t.startswith("query: ") for t in fake_model.calls[0])

    def test_embeds_without_any_key(self, no_key, fake_model):
        assert _embed_query(QUERY) == [0.1, 0.2, 0.3]

    def test_a_broken_local_model_returns_no_vector(self, monkeypatch):
        monkeypatch.setattr(embeddings._state, "model", _FakeModel(fail=True), raising=False)
        assert _embed_query(QUERY) == []

    def test_logs_one_warning_naming_the_reason(self, monkeypatch, caplog):
        monkeypatch.setattr(embeddings._state, "model", _FakeModel(fail=True), raising=False)
        with caplog.at_level("WARNING", logger="planner.pipeline"):
            assert _embed_query(QUERY) == []
        warnings = _warnings(caplog, "planner.pipeline")
        assert len(warnings) == 1
        assert "local model" in warnings[0]


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


class TestHttpRefusal:
    """Without a reader the planning endpoints refuse; the catalogue does not."""

    @pytest.mark.parametrize(
        "path,body",
        [
            ("/routes/generate", {"query": QUERY}),
            ("/routes/reroute", {"point_ids": [1, 2]}),
            ("/routes/explain", {"point_ids": [1, 2]}),
        ],
    )
    def test_planning_needs_a_reader(self, no_key, _restore_planner, path, body):
        client = _client_with_planner(_StubPlanner())
        r = client.post(path, json=body)
        assert r.status_code == 503
        assert r.json()["detail"]["reason"] == "llm_not_configured"

    def test_the_refusal_comes_before_any_work(self, no_key, _restore_planner):
        """The stub would fail loudly if the planner were reached at all."""
        client = _client_with_planner(_StubPlanner())
        r = client.post("/routes/generate", json={"query": QUERY})
        assert r.status_code == 503

    def test_the_catalogue_answers_without_a_reader(self, no_key, _restore_planner):
        class _Catalogue:
            db = _FakeDB()

        client = _client_with_planner(_Catalogue())
        assert client.get("/places").status_code != 503


class TestHttpDegraded:

    def test_escaped_upstream_error_is_503_not_500(self, with_key, _restore_planner):
        client = _client_with_planner(
            _StubPlanner(UpstreamUnavailable("valhalla: /route failed: 502"))
        )
        r = client.post("/routes/generate", json={"query": QUERY})
        assert r.status_code == 503
        assert "502" in r.json()["detail"]

    def test_a_degraded_upstream_is_never_a_bare_500(self, with_key, _restore_planner):
        client = _client_with_planner(_StubPlanner(UpstreamUnavailable("db: gone")))
        assert client.post("/routes/generate", json={"query": QUERY}).status_code == 503

    def test_planner_errors_keep_their_own_status(self, with_key, _restore_planner):
        client = _client_with_planner(
            _StubPlanner(NoCandidatesFound("no candidates matched the query"))
        )
        r = client.post("/routes/generate", json={"query": QUERY})
        assert r.status_code == 404
        assert "no candidates" in r.json()["detail"]

    def test_health_without_a_key_says_so(
            self, no_key, monkeypatch, fake_model, _restore_planner):
        body = _client_with_planner(_HealthOnly(monkeypatch)).get("/health").json()
        assert body["embedder"] is True
        assert body["llm"] is False
        assert body["db"] is True
        assert body["valhalla"] is True
        assert body["status"] == "degraded", "no reader is not a healthy planner"

    def test_health_flags_are_honest_with_a_key(
            self, with_key, monkeypatch, fake_model, _restore_planner):
        body = _client_with_planner(_HealthOnly(monkeypatch)).get("/health").json()
        assert body["llm"] is True
        assert body["embedder"] is True
        assert body["status"] == "ok"

    def test_health_is_degraded_when_the_local_model_cannot_load(
            self, monkeypatch, _restore_planner):
        monkeypatch.setattr(embeddings._state, "model", None, raising=False)
        monkeypatch.setattr(embeddings._state, "available", False, raising=False)
        body = _client_with_planner(_HealthOnly(monkeypatch)).get("/health").json()
        assert body["embedder"] is False
        assert body["status"] == "degraded"


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


class TestLiveRefusal:
    """The deliverable: without a key the pipeline refuses instead of guessing."""

    def test_planning_refuses_without_a_key(self, no_key, live_db):
        with offline():
            with pytest.raises(InterpretationUnavailable):
                Pipeline(db=live_db).generate(
                    GenerateReq(query=QUERY, time_budget_minutes=120)
                )

    def test_the_vector_signal_is_skipped_without_a_query_vector(self, live_db, monkeypatch):
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


def test_a_route_that_cannot_be_planned_is_an_answer_not_a_failed_request(with_key):
    """«Cannot plan here» must not leave as HTTP 422 with the optimizer's sentence.

    A refusal to plan is an ordinary response: status infeasible, no points, a reason code.
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
    assert "optimizer" not in (body.get("explanation") or "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

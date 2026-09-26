"""Degraded mode: no OPENROUTER_API_KEY (or an OpenRouter that is down) must
never turn a route request into a 500.

The bug this file pins down, reproduced on a machine with no key:

    File "agent/planner/intent.py", line 144, in extract_intent
      answers = jev.ask(query, _QUESTIONS)
    File "agent/jev.py", line 49, in ask
      raise RuntimeError("OPENROUTER_API_KEY not set — Jev unavailable")
    → HTTP 500 on every POST /routes/generate, while the README promised
      "OPENROUTER_API_KEY is optional — without it the pipeline degrades to
      keyword-only retrieval".

What must hold, per step:
  * intent  — falls back to a deterministic parse of the query text: categories
    from the shared keyword→category map, an explicit duration kept, named
    places still extracted.
  * rerank  — skipped, retrieval order kept, ONE warning (not one per
    candidate).
  * embed   — no vector, retrieval runs keyword/category-only.
  * health  — still reports llm/embedder false, so the flags stay honest.
  * HTTP    — an upstream error that escapes the planner is a 503 with a
    detail, never an opaque 500.

And what must NOT change: with a key and a working upstream, intent and
rerank produce exactly what Jev answered.

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

from agent import constants, jev, main as agent_main
from agent.config import settings
from agent.errors import NoCandidatesFound
from agent.jev import JevUnavailableError, JevUpstreamError
from agent.models import Candidate, GenerateReq, ResolvedConstraints
from agent.planner import intent as intent_mod, pipeline as pipeline_mod, retrieve as retrieve_mod
from agent.planner.intent import extract_intent, fallback_intent
from agent.planner.pipeline import Pipeline, _openrouter_embed
from agent.planner.rerank import rerank
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
    assert jev.available() is False


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-test-key", raising=False)
    assert jev.available() is True


@contextmanager
def offline() -> Iterator[None]:
    """Belt and braces: nothing in this block may reach OpenRouter, whatever
    the environment says."""
    old = jev.api_key
    jev.api_key = lambda: None  # type: ignore[assignment]
    try:
        yield
    finally:
        jev.api_key = old  # type: ignore[assignment]


def _c(id: int, relevance: float = 0.5, name: str = "place") -> Candidate:
    return Candidate(id=id, name=name, category="замок", lat=53.68, lon=23.82,
                     relevance=relevance, rrf_score=relevance)


def _mock_jev_ask(_query: str, _questions: dict) -> dict:
    """The typed-answer shape a real /systemone call returns."""
    return {
        **{f"cat_{cat}": {"noul": 0.0, "confidence": 0.9} for cat in constants.CATEGORIES},
        **{f"neg_{cat}": {"noul": 0.0, "confidence": 0.9} for cat in constants.CATEGORIES},
        "intent_type": {"choice": "themed", "confidence": 0.9},
        "party_type": {"choice": "solo", "confidence": 0.9},
        "era_hint": {"choice": "any", "confidence": 0.9},
        "search_scope": {"choice": "town", "confidence": 0.9},
        "mentions_named_place": {"noul": 1.0, "confidence": 0.9},
        "time_hours": {"score": 2, "confidence": 0.9},
    }


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
# jev client — the key lookup, and the two degradable failure classes
# ─────────────────────────────────────────────────────────────────────────────

class TestJevClient:

    def test_no_key_raises_unavailable(self, no_key):
        with pytest.raises(JevUnavailableError):
            jev.ask("костёлы", {"q": {"type": "noul"}})
        # Still a RuntimeError, so callers written against the old client
        # (scripts/enrich_places.py) keep catching it.
        assert issubclass(JevUnavailableError, RuntimeError)

    def test_both_causes_are_one_catchable_type(self):
        # Callers degrade on the base class, so "no key" and "upstream down"
        # must not be two different except clauses.
        assert issubclass(JevUnavailableError, jev.JevError)
        assert issubclass(JevUpstreamError, jev.JevError)

    def test_key_present_and_callable_means_available(self, with_key):
        assert jev.available() is True

    def test_timeout_becomes_upstream_error(self, with_key, monkeypatch):
        monkeypatch.setattr(httpx, "Client", _boom_client(httpx.ReadTimeout("timed out")))
        with pytest.raises(JevUpstreamError) as exc:
            jev.ask("костёлы", {"q": {"type": "noul"}})
        assert "timed out" in str(exc.value)

    def test_5xx_becomes_upstream_error(self, with_key, monkeypatch):
        monkeypatch.setattr(httpx, "Client", _fake_post_client(_Resp(status=502)))
        with pytest.raises(JevUpstreamError):
            jev.ask("костёлы", {"q": {"type": "noul"}})

    def test_malformed_body_still_raises_loudly(self, with_key, monkeypatch):
        """A 200 whose body is not an answers map is a contract break, not an
        outage: it keeps propagating as ValueError (behaviour unchanged)."""
        monkeypatch.setattr(httpx, "Client", _fake_post_client(_Resp(body={"nope": 1})))
        with pytest.raises(ValueError, match="malformed response"):
            jev.ask("костёлы", {"q": {"type": "noul"}})

    def test_a_good_answer_is_returned_untouched(self, with_key, monkeypatch):
        answers = {"cat_замок": {"noul": 0.98}}
        monkeypatch.setattr(httpx, "Client", _fake_post_client(_Resp(body={"answers": answers})))
        assert jev.ask("костёлы", {"cat_замок": {"type": "noul"}}) == answers


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — intent degrades to a deterministic parse of the same text
# ─────────────────────────────────────────────────────────────────────────────

class TestIntentFallback:

    def test_no_key_never_calls_openrouter(self, no_key, monkeypatch):
        monkeypatch.setattr(jev, "ask", _explode)
        res = extract_intent(QUERY)          # used to raise → HTTP 500
        assert res.source == "regex"
        assert res.raw_response is None
        assert res.confidence == 0.0
        assert res.decision.intent_type in constants.INTENT_TYPES

    def test_no_key_categories_come_from_the_shared_map(self, no_key, monkeypatch):
        """The fallback fills categories from the deterministic keyword→category
        map (resolve.CATEGORY_SYNONYMS) — the same taxonomy retrieval uses —
        not from a model guess.  «замкам» → замок, so "замки Гродно" still
        retrieves castles in degraded mode.  Exclusion ("без замков") and
        keywords stay a no-invent zone: the maps do not carry them."""
        monkeypatch.setattr(jev, "ask", _explode)
        d = extract_intent(QUERY).decision
        assert "замок" in d.categories_pos
        assert d.categories_neg == []
        assert d.keywords_pos == []
        assert d.keywords_neg == []

    def test_no_key_keeps_the_named_places(self, no_key):
        d = fallback_intent(QUERY).decision
        assert "Гродно" in d.named_places      # resolves through the DB path
        # Region names are still filtered out (same regex as the Jev path).
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

    def test_logs_one_warning_per_call(self, no_key, monkeypatch, caplog):
        monkeypatch.setattr(jev, "ask", _explode)
        with caplog.at_level("WARNING", logger="agent.planner.intent"):
            extract_intent(QUERY)
        warnings = _warnings(caplog, "agent.planner.intent")
        assert len(warnings) == 1, "one warning per call, not per candidate"
        assert "fallback" in warnings[0]

    def test_upstream_failure_also_degrades(self, with_key, monkeypatch, caplog):
        def boom(*_a, **_kw):
            raise JevUpstreamError("jev: /systemone failed: 502")
        monkeypatch.setattr(jev, "ask", boom)
        with caplog.at_level("WARNING", logger="agent.planner.intent"):
            res = extract_intent(QUERY)          # must not propagate
        assert res.source == "regex"
        assert "Гродно" in res.decision.named_places
        assert len(_warnings(caplog, "agent.planner.intent")) == 1

    def test_with_a_key_the_jev_path_is_unchanged(self, with_key, monkeypatch):
        monkeypatch.setattr(jev, "ask", _mock_jev_ask)
        res = extract_intent(QUERY)
        assert res.source == "jev"
        d = res.decision
        assert d.intent_type == "themed"        # straight from the mock
        assert d.categories_pos == []
        assert d.search_scope == "town"
        assert d.era_hint == "any"
        assert d.party_type == "solo"
        assert d.named_places == ["Хочу", "Гродно"]
        assert res.confidence == 0.9
        # The mock scored 2 hours, but the query states no time — the model
        # guess is dropped, exactly as before this change.
        assert d.time_budget_minutes is None

    def test_both_paths_agree_on_a_stated_duration(self, with_key, monkeypatch):
        monkeypatch.setattr(jev, "ask", _mock_jev_ask)
        with_jev = extract_intent("погулять по костёлам 2 часа").decision.time_budget_minutes
        monkeypatch.setattr(jev, "ask", _explode)
        monkeypatch.setattr(jev, "available", lambda: False)
        without_jev = extract_intent("погулять по костёлам 2 часа").decision.time_budget_minutes
        assert with_jev == without_jev == 120

    def test_with_a_key_a_broken_answer_still_raises(self, with_key, monkeypatch):
        """Behaviour with a key must not change: taxonomy drift fails loud."""
        def drifted(*_a, **_kw):
            answers = _mock_jev_ask("", {})
            answers["intent_type"] = {"choice": "not-a-type"}
            return answers
        monkeypatch.setattr(jev, "ask", drifted)
        with pytest.raises(ValueError, match="unknown intent_type"):
            extract_intent(QUERY)

    def test_source_stays_inside_the_model_literal(self):
        assert fallback_intent(QUERY).source == "regex"
        assert intent_mod._fallback_time_budget("2 часа") == 120


# ─────────────────────────────────────────────────────────────────────────────
# Step 3.5 — rerank is skipped, the retrieval order stands
# ─────────────────────────────────────────────────────────────────────────────

class TestRerankDegraded:

    def test_no_key_keeps_the_retrieval_order(self, no_key, monkeypatch):
        monkeypatch.setattr(jev, "ask", _explode)
        pool = [_c(1, 0.9), _c(2, 0.8), _c(3, 0.7), _c(4, 0.1)]
        out = rerank(QUERY, pool, top_k=2)
        assert [c.id for c in out] == [1, 2, 3, 4]   # NOT truncated to top_k
        assert [c.relevance for c in out] == [0.9, 0.8, 0.7, 0.1]  # untouched
        assert all(c.rerank_score is None for c in out)

    def test_no_key_logs_one_warning_for_the_whole_pool(self, no_key, monkeypatch, caplog):
        monkeypatch.setattr(jev, "ask", _explode)
        pool = [_c(i) for i in range(constants.RERANK_POOL_SIZE)]
        with caplog.at_level("WARNING", logger="agent.planner.rerank"):
            rerank(QUERY, pool, top_k=constants.RERANK_POOL_SIZE)
        assert len(_warnings(caplog, "agent.planner.rerank")) == 1

    def test_upstream_failure_keeps_the_retrieval_order(self, with_key, monkeypatch):
        def boom(*_a, **_kw):
            raise JevUpstreamError("jev: /systemone failed: timeout")
        monkeypatch.setattr(jev, "ask", boom)
        out = rerank(QUERY, [_c(7, 0.4), _c(8, 0.2)], top_k=1)
        assert [c.id for c in out] == [7, 8]
        assert all(c.rerank_score is None for c in out)

    def test_empty_pool_short_circuits(self, no_key, monkeypatch):
        monkeypatch.setattr(jev, "ask", _explode)
        assert rerank(QUERY, [], top_k=5) == []

    def test_with_a_key_the_scores_still_win(self, with_key, monkeypatch):
        """Unchanged behaviour: relevance is overwritten, pool cut to top_k."""
        def scored(_state, _questions, **_kw):
            # rel_1 is the best candidate, rel_0 the worst → the order flips.
            return {"rel_0": {"score": 0.0}, "rel_1": {"score": 4.0}, "rel_2": {"score": 2.0}}
        monkeypatch.setattr(jev, "ask", scored)
        out = rerank(QUERY, [_c(1), _c(2), _c(3)], top_k=2)
        assert [c.id for c in out] == [2, 3]
        assert [c.relevance for c in out] == [1.0, 0.5]


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
        client = _client_with_planner(_StubPlanner(JevUpstreamError("jev: failed: 502")))
        r = client.post("/routes/generate", json={"query": QUERY})
        assert r.status_code == 503
        assert "OpenRouter unavailable" in r.json()["detail"]

    def test_missing_key_error_is_503_too(self, _restore_planner):
        client = _client_with_planner(_StubPlanner(JevUnavailableError("no key")))
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
        assert resp.debug["intent_source"] == "regex"
        assert len(resp.points) >= 2, "a route needs at least two stops"
        assert all(p.lat and p.lon for p in resp.points)
        assert resp.explanation

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

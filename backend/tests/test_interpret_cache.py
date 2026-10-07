"""The reading is cached; the verdicts, the measurements and the geometry are not.

A reading is a function of the text, the visible UI filters and the prompt — the
things that go into the key — so two requests may share one model call only when
the model would have been asked exactly the same question. Everything else about
a request is an answer about the world (the database, Valhalla) and is never
cached: a stale verdict in the panel would be a lie, a stale reading is at worst
a question asked twice.

The dangerous part is not the lookup but the sharing: `TripRequirements` is
mutated downstream by resolve() and the verifier, so a stored object handed out
directly would make the next request inherit this one's place ids and verdicts.

No network, no DB: the agent is replaced by a fixed contract.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import trace
from agent.models import GenerateReq
from agent.planner import intent, interpret_cache as cache
from agent.requirements import Requirement, TripRequirements


def _req(**over: Any) -> GenerateReq:
    body: dict[str, Any] = {"query": "старый город за два часа", "locale": "ru"}
    body.update(over)
    return GenerateReq(**body)


def _contract(name: str = "Старый замок") -> TripRequirements:
    return TripRequirements(
        locale="ru",
        raw_query="старый город за два часа",
        requirements=[Requirement(kind="must_visit", name=name, source="text")],
        source="llm",
    )


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    monkeypatch.delenv("CACHE_BUST", raising=False)
    cache.INTERPRET_CACHE.clear()
    cache.INTERPRET_CACHE.hits = 0
    cache.INTERPRET_CACHE.misses = 0
    yield
    cache.INTERPRET_CACHE.clear()


# ── the key ──────────────────────────────────────────────────────────────────

def test_the_same_question_gets_the_same_key():
    instructions = "ты читаешь запрос"
    assert cache.interpret_key("замки", _req(), instructions) == cache.interpret_key(
        "замки", _req(), instructions
    )


def test_a_changed_ui_filter_is_a_different_question():
    """The panel's controls go into the prompt, so they must go into the key."""
    instructions = "ты читаешь запрос"
    base = cache.interpret_key("замки", _req(), instructions)

    for changed in (
        _req(interests=["кафе"]),
        _req(hard_services=["туалет"]),
        _req(avoid=["музей"]),
        _req(time_budget_minutes=60),
        _req(party_children=2),
        _req(party_children_ages=[4, 7]),
        _req(mobility=["stroller"]),
        _req(result_mode="catalogue"),
        _req(locale="en"),
    ):
        assert cache.interpret_key(changed.query, changed, instructions) != base


def test_the_travel_profile_is_not_part_of_the_question():
    """«пешком» and «на машине» are the same reading — only the route differs."""
    instructions = "ты читаешь запрос"
    walk = _req(profile="pedestrian")
    drive = _req(profile="car")

    assert cache.interpret_key(walk.query, walk, instructions) == cache.interpret_key(
        drive.query, drive, instructions
    )


def test_editing_the_prompt_invalidates_every_entry():
    before = cache.interpret_key("замки", _req(), "один промпт")
    after = cache.interpret_key("замки", _req(), "другой промпт")

    assert before != after


def test_switching_the_model_invalidates_every_entry():
    """A reading is the MODEL's output, so a different model is a different answer.

    Without the model in the key, a process that switched models kept answering
    from the previous model's readings — cheap to miss, expensive to believe:
    it makes every "we measured the new model" claim false.
    """
    instructions = "ты читаешь запрос"
    baseline = cache.interpret_key("замки", _req(), instructions, "google/gemini-2.5-pro")

    assert cache.interpret_key(
        "замки", _req(), instructions, "google/gemini-2.5-flash"
    ) != baseline
    assert cache.interpret_key("замки", _req(), instructions, None) != baseline


# ── the store ────────────────────────────────────────────────────────────────

def test_an_expired_reading_is_not_returned():
    store = cache.TtlLru(maxsize=4, ttl_s=1)
    store.put("k", _contract())
    store._entries["k"].stored_at = time.monotonic() - 5

    assert store.get("k") is None
    assert store.stats()["expired"] == 1


def test_the_store_stays_bounded():
    store = cache.TtlLru(maxsize=2, ttl_s=60)
    for key in ("a", "b", "c"):
        store.put(key, _contract(key))

    assert store.stats()["size"] == 2
    assert store.get("a") is None  # the oldest went first
    assert store.get("c") is not None


def test_cache_bust_switches_the_store_off(monkeypatch):
    monkeypatch.setenv("CACHE_BUST", "1")
    store = cache.TtlLru(maxsize=4, ttl_s=60)

    store.put("k", _contract())

    assert store.get("k") is None
    assert store.stats()["enabled"] is False


def test_stats_are_countable_not_claimed():
    store = cache.TtlLru(maxsize=4, ttl_s=60)
    store.put("k", _contract())

    store.get("k")
    store.get("k")
    store.get("missing")

    stats = store.stats()
    assert (stats["hits"], stats["misses"], stats["hit_rate"]) == (2, 1, 0.667)


# ── the part that would bite: shared state ───────────────────────────────────

def test_the_second_reading_does_not_inherit_the_first_ones_verdicts(monkeypatch):
    """A cached contract must be handed out as a copy.

    Downstream the contract is mutated: place ids are attached, statuses are
    written by the verifier. If the stored object were returned as-is, the next
    request would start with the previous request's answers already in it — and
    would report them as its own.
    """
    calls = {"n": 0}

    def fake_agent(query, req, db, wall_clock_s=None):
        calls["n"] += 1
        return _contract()

    monkeypatch.setattr(intent, "_agent_contract", fake_agent)
    monkeypatch.setattr(
        intent,
        "_interpret_cache_key",
        lambda query, req: ("test-key", "prompt-hash"),
    )

    first = intent.build_requirements("старый город за два часа", _req())
    # What the pipeline does to it right after the reading.
    first.requirements[0].place_id = 777
    first.requirements[0].status = "satisfied"

    second = intent.build_requirements("старый город за два часа", _req())

    assert calls["n"] == 1, "второй запрос должен был обойтись без модели"
    assert second.requirements[0].place_id is None
    assert second.requirements[0].status == "pending"
    assert first.requirements[0].place_id == 777  # the caller keeps its own


def test_without_an_agent_nothing_is_cached(monkeypatch):
    """The deterministic parse is milliseconds of regex; caching it is pointless."""
    monkeypatch.setattr(
        intent, "_interpret_cache_key", lambda query, req: (None, "")
    )

    intent.build_requirements("замки Гродно", _req())

    assert cache.INTERPRET_CACHE.stats()["size"] == 0


def test_a_cached_reading_says_so_in_the_trace(monkeypatch):
    """No model call happened, and the trace must not leave that to guesswork.

    A hit that records nothing looks exactly like a call nobody recorded — the
    one reading of the trace that is wrong.
    """

    def fake_agent(query, req, db, wall_clock_s=None):
        return _contract()

    monkeypatch.setattr(intent, "_agent_contract", fake_agent)
    monkeypatch.setattr(
        intent, "_interpret_cache_key", lambda query, req: ("trace-key", "prompt-hash")
    )

    intent.build_requirements("старый город за два часа", _req())  # fills the cache, untraced

    trace.begin("job-cache")
    try:
        intent.build_requirements("старый город за два часа", _req())
        span, = trace._traces["job-cache"].spans
    finally:
        trace.finish()

    assert span.name == "interpret · model"
    assert span.status == "skipped"  # the step did not run; it is not an error
    assert span.facts == {"cached": True}

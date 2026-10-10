"""The reading is cached; the verdicts, the measurements and the geometry are not.

No network, no DB: the agent is replaced by a fixed contract.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import interpret_cache as cache
from contracts.planner import GenerateReq
from core.errors import InterpretationUnavailable
from domain.requirements import Requirement, TripRequirements
from planner import intent
from telemetry import trace


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

    Without the model in the key, "we measured the new model" claims become false.
    """
    instructions = "ты читаешь запрос"
    baseline = cache.interpret_key("замки", _req(), instructions, "google/gemini-2.5-pro")

    assert cache.interpret_key(
        "замки", _req(), instructions, "google/gemini-2.5-flash"
    ) != baseline
    assert cache.interpret_key("замки", _req(), instructions, None) != baseline


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
    assert store.get("a") is None
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


def test_the_second_reading_does_not_inherit_the_first_ones_verdicts(monkeypatch):
    """A cached contract must be handed out as a copy.

    Downstream it is mutated: place ids attached, statuses written by the verifier.
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
    first.requirements[0].place_id = 777
    first.requirements[0].status = "satisfied"

    second = intent.build_requirements("старый город за два часа", _req())

    assert calls["n"] == 1, "второй запрос должен был обойтись без модели"
    assert second.requirements[0].place_id is None
    assert second.requirements[0].status == "pending"
    assert first.requirements[0].place_id == 777


def test_without_a_cache_key_nothing_is_cached(monkeypatch):
    """No key means no reading at all — and nothing to cache."""
    monkeypatch.setattr(
        intent, "_interpret_cache_key", lambda query, req: (None, "")
    )

    with pytest.raises(InterpretationUnavailable):
        intent.build_requirements("замки Гродно", _req())

    assert cache.INTERPRET_CACHE.stats()["size"] == 0


def test_a_cached_reading_says_so_in_the_trace(monkeypatch):
    """No model call happened, and the trace must not leave that to guesswork.

    A hit that records nothing looks exactly like a call nobody recorded.
    """

    def fake_agent(query, req, db, wall_clock_s=None):
        return _contract()

    monkeypatch.setattr(intent, "_agent_contract", fake_agent)
    monkeypatch.setattr(
        intent, "_interpret_cache_key", lambda query, req: ("trace-key", "prompt-hash")
    )

    intent.build_requirements("старый город за два часа", _req())

    trace.begin("job-cache")
    try:
        intent.build_requirements("старый город за два часа", _req())
        span, = trace._traces["job-cache"].spans
    finally:
        trace.finish()

    assert span.name == "interpret · model"
    assert span.status == "skipped"
    assert span.facts == {"cached": True}

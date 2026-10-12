"""The deterministic core, exercised without a model.

`plan_walk` is what the agent's judgement is measured against: it orders the stops
it is handed and reports the numbers. If it cannot produce a sane walk from a
handful of real place ids, no prompt will fix that — so this is the first thing
to prove, and it needs neither a key nor a network.
"""

from __future__ import annotations

import pytest

# Grodno's old town — a walk of a few hundred metres, not a tour of the oblast.
OLD_TOWN = (53.6778, 23.8295)


def _sight_ids(repos, limit: int = 5) -> list[int]:
    """Hand-picked stops: real rows near one another, chosen by a query, not by a model.

    Deliberately a *cluster*: castles across the region are not a walk, and the
    routing engine is right to refuse them.
    """
    found = repos.places.nearby(OLD_TOWN[0], OLD_TOWN[1], radius_km=1.5, limit=limit)
    ids = [row.id for row in found if row.id is not None]
    if len(ids) < 3:
        pytest.skip("not enough places in the dataset to build a walk")
    return ids


def test_a_handful_of_stops_becomes_a_measured_walk(db_repos) -> None:
    from agent.tools.plan import plan_walk

    ids = _sight_ids(db_repos)
    answer = plan_walk(ids, repos=db_repos, budget_minutes=180)

    assert answer.get("error") is None, answer.get("message")
    assert answer["count"] >= 2
    assert len(answer["results"]) == answer["count"]

    # Every stop that comes back is one that was asked for, and none is invented.
    returned = {stop["id"] for stop in answer["results"]}
    assert returned <= set(ids)

    # The arithmetic is reported, not guessed, and the budget verdict is a bool.
    assert answer["walk_minutes"] >= 0
    assert answer["total_minutes"] >= answer["walk_minutes"]
    assert isinstance(answer["fits_budget"], bool)
    assert answer["round_trip"] is False


def test_too_few_stops_is_an_error_not_a_crash(db_repos) -> None:
    from agent.tools.plan import plan_walk

    answer = plan_walk([1], repos=db_repos, budget_minutes=120)
    assert answer["error"] is not None
    assert answer["results"] == []


def test_must_keep_stops_are_never_dropped(db_repos) -> None:
    """A refinement's pinned stop survives a budget that cannot hold everything."""
    from agent.tools.plan import plan_walk

    ids = _sight_ids(db_repos, limit=6)
    pinned = [ids[0]]
    answer = plan_walk(ids, repos=db_repos, budget_minutes=15, must_keep_ids=pinned)

    assert answer.get("error") in (None, "no_route"), answer.get("message")
    if answer.get("error") is None:
        assert pinned[0] in {stop["id"] for stop in answer["results"]}


def test_the_same_stops_are_measured_once(db_repos) -> None:
    """Valhalla is deterministic: the same stops and budget are a cache hit."""
    from agent.tools.plan import plan_walk
    from core import cache

    ids = _sight_ids(db_repos)
    first = plan_walk(ids, repos=db_repos, budget_minutes=180)
    assert first.get("error") is None, first.get("message")

    hits_before = cache.MEASURE_CACHE.hits
    second = plan_walk(ids, repos=db_repos, budget_minutes=180)

    assert cache.MEASURE_CACHE.hits == hits_before + 1
    assert second["count"] == first["count"]


def test_the_measure_key_carries_the_data_version() -> None:
    """The version is part of the key: order-insensitive in stops, but a new
    version is a new measurement."""
    from agent.tools.plan import _measure_key

    same = _measure_key([3, 1, 2], 60, "pedestrian", False, None, "ru", None, "v1")
    reordered = _measure_key([1, 2, 3], 60, "pedestrian", False, None, "ru", None, "v1")
    bumped = _measure_key([1, 2, 3], 60, "pedestrian", False, None, "ru", None, "v2")

    assert same == reordered
    assert same != bumped


def test_a_data_version_bump_re_measures_instead_of_replaying(db_repos, monkeypatch) -> None:
    """A re-seed is not a cache hit: the bump goes back to the rows.

    The seed's version is faked here — the point is the key's behavior, not a live
    re-seed (which the shared database must not be asked to perform). The same
    version is a hit; a different one misses, and misses by *reading the rows
    again* rather than returning the earlier numbers.
    """
    from agent.tools.plan import plan_walk
    from core import cache

    ids = _sight_ids(db_repos, limit=4)
    monkeypatch.setattr(db_repos.datasets, "version", lambda: "before")
    first = plan_walk(ids, repos=db_repos, budget_minutes=180)
    assert first.get("error") is None, first.get("message")

    hits = cache.MEASURE_CACHE.hits
    repeat = plan_walk(ids, repos=db_repos, budget_minutes=180)
    assert cache.MEASURE_CACHE.hits == hits + 1  # same version → HIT
    assert repeat["count"] == first["count"]

    # A bumped version must not replay the earlier answer: count the row reads.
    reads = {"n": 0}
    original = db_repos.places.get_by_ids

    def counting(wanted):
        reads["n"] += 1
        return original(wanted)

    monkeypatch.setattr(db_repos.places, "get_by_ids", counting)
    monkeypatch.setattr(db_repos.datasets, "version", lambda: "after")

    hits = cache.MEASURE_CACHE.hits
    again = plan_walk(ids, repos=db_repos, budget_minutes=180)

    assert reads["n"] >= 1  # it went back to the rows, not the cache
    assert cache.MEASURE_CACHE.hits == hits  # ... and it was a miss
    assert again["count"] == first["count"]

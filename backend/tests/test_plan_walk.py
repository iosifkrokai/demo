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

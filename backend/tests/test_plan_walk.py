"""The deterministic core, exercised without a model."""

from __future__ import annotations

import pytest

from core import constants

OLD_TOWN = (53.6778, 23.8295)


def _sight_ids(repos, limit: int = 5) -> list[int]:
    """Hand-picked stops: real rows near one another, chosen by a query, not by a model."""
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

    returned = {stop["id"] for stop in answer["results"]}
    assert returned <= set(ids)

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


def test_the_profile_reaches_the_engine(db_repos) -> None:
    """A bicycle is not a walk of the same stops."""
    from agent.tools.plan import plan_walk

    found = db_repos.places.nearby(OLD_TOWN[0], OLD_TOWN[1], radius_km=1.5, limit=2)
    ids = [row.id for row in found if row.id is not None]
    if len(ids) < 2:
        pytest.skip("not enough places in the dataset to measure a pair")

    walk = plan_walk(ids, repos=db_repos, budget_minutes=180, profile="pedestrian")
    bike = plan_walk(ids, repos=db_repos, budget_minutes=180, profile="bicycle")
    car = plan_walk(ids, repos=db_repos, budget_minutes=180, profile="car")

    assert walk.get("error") is None, walk.get("message")
    assert bike.get("error") is None, bike.get("message")

    assert walk["provenance"]["profile"] == "pedestrian"
    assert bike["provenance"]["profile"] == "bicycle"
    assert car["provenance"]["profile"] == "auto"

    assert {s["id"] for s in bike["results"]} == {s["id"] for s in walk["results"]}
    assert (bike["length_km"], bike["walk_minutes"]) != (
        walk["length_km"],
        walk["walk_minutes"],
    )


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


def test_the_measure_key_carries_the_ordering_regime() -> None:
    """A run that skips the Valhalla re-order measures a different walk from the
    same stops, so it must not be served the other regime's entry."""
    from agent.tools.plan import _measure_key, _order_regime
    from routing.models import LatLon

    ordered = _measure_key([1, 2, 3], 60, "pedestrian", False, None, "ru", None, "v1")
    skipped = _measure_key([1, 2, 3], 60, "pedestrian", False, None, "ru", None, "v1", "noorder")

    assert ordered != skipped

    assert _order_regime(None, None) == "order"
    assert _order_regime(constants.VALHALLA_ORDER_MIN_LEFT_S - 1, None) == "noorder"
    assert _order_regime(60.0, None) == "order"
    assert _order_regime(None, LatLon(lat=53.6771, lon=23.8258)) == "noorder"


def test_a_data_version_bump_re_measures_instead_of_replaying(db_repos, monkeypatch) -> None:
    """A re-seed is not a cache hit: the bump goes back to the rows."""
    from agent.tools.plan import plan_walk
    from core import cache

    ids = _sight_ids(db_repos, limit=4)
    monkeypatch.setattr(db_repos.datasets, "version", lambda: "before")
    first = plan_walk(ids, repos=db_repos, budget_minutes=180)
    assert first.get("error") is None, first.get("message")

    hits = cache.MEASURE_CACHE.hits
    repeat = plan_walk(ids, repos=db_repos, budget_minutes=180)
    assert cache.MEASURE_CACHE.hits == hits + 1
    assert repeat["count"] == first["count"]

    reads = {"n": 0}
    original = db_repos.places.get_by_ids

    def counting(wanted):
        reads["n"] += 1
        return original(wanted)

    monkeypatch.setattr(db_repos.places, "get_by_ids", counting)
    monkeypatch.setattr(db_repos.datasets, "version", lambda: "after")

    hits = cache.MEASURE_CACHE.hits
    again = plan_walk(ids, repos=db_repos, budget_minutes=180)

    assert reads["n"] >= 1
    assert cache.MEASURE_CACHE.hits == hits
    assert again["count"] == first["count"]

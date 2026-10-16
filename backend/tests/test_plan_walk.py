"""The deterministic core, exercised without a model."""

from __future__ import annotations

import pytest

from core import constants

OLD_TOWN = (53.6778, 23.8295)
SCAN = 200


def _facts(repos):
    from routing.models import CategoryFacts

    return CategoryFacts.from_rows(repos.taxonomy.all())


def _nearby(repos, limit: int = SCAN) -> list:
    return repos.places.nearby(OLD_TOWN[0], OLD_TOWN[1], radius_km=1.5, limit=limit)


def _sight_ids(repos, limit: int = 5) -> list[int]:
    """Hand-picked stops: real rows near one another, chosen by a query, not by a model.

    Services are left out: the walk is not measured through them, so asking for a café
    would make the route shorter than the ids and the count assertions below untrue.
    The old town's nearest rows are mostly services, hence the wide scan.
    """
    facts = _facts(repos)
    ids = [
        row.id
        for row in _nearby(repos)
        if row.id is not None and not facts.is_service(row.category)
    ][:limit]
    if len(ids) < 2:
        pytest.skip("not enough sights in the dataset to build a walk")
    return ids


def _service_ids(repos, count: int = 1) -> list[int]:
    facts = _facts(repos)
    ids = [
        row.id for row in _nearby(repos) if row.id is not None and facts.is_service(row.category)
    ]
    if len(ids) < count:
        pytest.skip("not enough services near the old town")
    return ids[:count]


def _service_id(repos) -> int:
    return _service_ids(repos)[0]


def test_a_handful_of_stops_becomes_a_measured_walk(db_repos) -> None:
    from agent.tools.plan import plan_walk
    from routing.models import WalkRequest

    ids = _sight_ids(db_repos)
    answer = plan_walk(WalkRequest(ids, budget_minutes=180), repos=db_repos)

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
    from routing.models import WalkRequest

    answer = plan_walk(WalkRequest([1], budget_minutes=120), repos=db_repos)
    assert answer["error"] is not None
    assert answer["results"] == []


def test_a_service_is_not_a_stop(db_repos) -> None:
    """A café or a hotel is where the walk passes, not where it stops."""
    from agent.tools.plan import plan_walk
    from routing.models import WalkRequest

    ids = [*_sight_ids(db_repos), _service_id(db_repos)]
    answer = plan_walk(WalkRequest(ids, budget_minutes=180), repos=db_repos)

    assert answer.get("error") is None, answer.get("message")
    assert ids[-1] not in {stop["id"] for stop in answer["results"]}
    assert answer["provenance"]["services_not_stops"]


def test_a_service_asked_for_by_id_stays_a_stop(db_repos) -> None:
    """A pinned service is a person saying they want to go there — the rule yields."""
    from agent.tools.plan import plan_walk
    from routing.models import WalkRequest

    service = _service_id(db_repos)
    ids = [service, *_sight_ids(db_repos)]
    answer = plan_walk(
        WalkRequest(ids, budget_minutes=180, must_keep_ids=[service]), repos=db_repos
    )

    assert answer.get("error") is None, answer.get("message")
    assert service in {stop["id"] for stop in answer["results"]}
    assert not answer["provenance"].get("services_not_stops")


def test_a_walk_of_nothing_but_services_says_so(db_repos) -> None:
    """The request is what has no stop, not the routing engine — the message says which."""
    from agent.tools.plan import plan_walk
    from routing.models import WalkRequest

    services = _service_ids(db_repos, count=2)

    answer = plan_walk(WalkRequest(services, budget_minutes=180), repos=db_repos)

    assert answer.get("error") == "no_route"
    assert answer["results"] == []
    assert answer["provenance"]["reason_code"] == "no_route_possible"
    assert "Остановками прогулки не становятся" in answer["message"]


def test_must_keep_stops_are_never_dropped(db_repos) -> None:
    """A refinement's pinned stop survives a budget that cannot hold everything."""
    from agent.tools.plan import plan_walk
    from routing.models import WalkRequest

    ids = _sight_ids(db_repos, limit=6)
    pinned = [ids[0]]
    answer = plan_walk(WalkRequest(ids, budget_minutes=15, must_keep_ids=pinned), repos=db_repos)

    assert answer.get("error") in (None, "no_route"), answer.get("message")
    if answer.get("error") is None:
        assert pinned[0] in {stop["id"] for stop in answer["results"]}


def test_the_profile_reaches_the_engine(db_repos) -> None:
    """A bicycle is not a walk of the same stops."""
    from agent.tools.plan import plan_walk
    from routing.models import WalkRequest

    ids = _sight_ids(db_repos, limit=2)

    walk = plan_walk(WalkRequest(ids, budget_minutes=180), repos=db_repos)
    bike = plan_walk(WalkRequest(ids, budget_minutes=180, profile="bicycle"), repos=db_repos)
    car = plan_walk(WalkRequest(ids, budget_minutes=180, profile="car"), repos=db_repos)

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
    from routing.models import WalkRequest

    ids = _sight_ids(db_repos)
    first = plan_walk(WalkRequest(ids, budget_minutes=180), repos=db_repos)
    assert first.get("error") is None, first.get("message")

    hits_before = cache.MEASURE_CACHE.hits
    second = plan_walk(WalkRequest(ids, budget_minutes=180), repos=db_repos)

    assert cache.MEASURE_CACHE.hits == hits_before + 1
    assert second["count"] == first["count"]


def test_the_measure_key_carries_the_data_version() -> None:
    """The version is part of the key: order-insensitive in stops, but a new
    version is a new measurement."""
    from agent.tools.plan import _measure_key
    from routing.models import WalkRequest

    same = _measure_key(WalkRequest([3, 1, 2], budget_minutes=60), data_version="v1")
    reordered = _measure_key(WalkRequest([1, 2, 3], budget_minutes=60), data_version="v1")
    bumped = _measure_key(WalkRequest([1, 2, 3], budget_minutes=60), data_version="v2")

    assert same == reordered
    assert same != bumped


def test_the_measure_key_carries_the_ordering_regime() -> None:
    """A run that skips the Valhalla re-order measures a different walk from the
    same stops, so it must not be served the other regime's entry."""
    from agent.tools.plan import _measure_key, _order_regime
    from routing.models import LatLon, WalkRequest

    ordered = _measure_key(WalkRequest([1, 2, 3], budget_minutes=60), data_version="v1")
    skipped = _measure_key(
        WalkRequest([1, 2, 3], budget_minutes=60), data_version="v1", regime="noorder"
    )

    assert ordered != skipped

    assert _order_regime(None, None) == "order"
    assert _order_regime(constants.VALHALLA_ORDER_MIN_LEFT_S - 1, None) == "noorder"
    assert _order_regime(60.0, None) == "order"
    assert _order_regime(None, LatLon(lat=53.6771, lon=23.8258)) == "noorder"


def test_a_data_version_bump_re_measures_instead_of_replaying(db_repos, monkeypatch) -> None:
    """A re-seed is not a cache hit: the bump goes back to the rows."""
    from agent.tools.plan import plan_walk
    from core import cache
    from routing.models import WalkRequest

    ids = _sight_ids(db_repos, limit=4)
    monkeypatch.setattr(db_repos.datasets, "version", lambda: "before")
    first = plan_walk(WalkRequest(ids, budget_minutes=180), repos=db_repos)
    assert first.get("error") is None, first.get("message")

    hits = cache.MEASURE_CACHE.hits
    repeat = plan_walk(WalkRequest(ids, budget_minutes=180), repos=db_repos)
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
    again = plan_walk(WalkRequest(ids, budget_minutes=180), repos=db_repos)

    assert reads["n"] >= 1
    assert cache.MEASURE_CACHE.hits == hits
    assert again["count"] == first["count"]

"""The detour is measured, not guessed — that is the whole point of the tool."""

from __future__ import annotations

import pytest

OLD_TOWN = (53.6778, 23.8295)

_SOVETSKAYA_LINE = {
    "type": "LineString",
    "coordinates": [[23.8295, 53.6778], [23.8330, 53.6790]],
}
_ALONG_LIMIT = 20


def _stops(repos, limit: int = 4) -> list[int]:
    found = repos.places.nearby(OLD_TOWN[0], OLD_TOWN[1], radius_km=1.0, limit=limit)
    ids = [row.id for row in found if row.id is not None]
    if len(ids) < 2:
        pytest.skip("not enough places in the dataset to build a route")
    return ids


def test_a_detour_comes_from_the_engine(db_repos) -> None:
    from agent.tools.services import nearby_services

    ids = _stops(db_repos)
    answer = nearby_services(ids, repos=db_repos)

    assert answer.get("error") is None, answer.get("message")
    if answer["provenance"]["candidates"] == 0:
        pytest.skip("no services beside this route in the dataset")

    for item in answer["results"]:
        assert item["detour_minutes"] >= 0
        assert item["insert_after"] in ids
        assert item["id"] not in ids
        assert item["name"]

    detours = [item["detour_minutes"] for item in answer["results"]]
    assert detours == sorted(detours), "the cheapest detour comes first"


def test_one_stop_is_not_a_route(db_repos) -> None:
    from agent.tools.services import nearby_services

    answer = nearby_services(_stops(db_repos)[:1], repos=db_repos)
    assert answer["error"] == "bad_argument"
    assert answer["results"] == []


def test_a_tight_detour_budget_narrows_the_answer(db_repos) -> None:
    from agent.tools.services import nearby_services

    ids = _stops(db_repos)
    answer = nearby_services(ids, repos=db_repos, max_detour_minutes=1)

    assert answer.get("error") is None, answer.get("message")
    assert all(item["detour_minutes"] <= 1 for item in answer["results"])


def _service_codes(db_repos) -> tuple[str, ...]:
    return tuple(db_repos.taxonomy.service_codes())


def test_the_service_default_is_unchanged(db_repos) -> None:
    """No categories named means the taxonomy's services — exactly what it always meant."""
    services = set(_service_codes(db_repos))
    answer = db_repos.places.services_along(
        _SOVETSKAYA_LINE,
        service_category_codes=_service_codes(db_repos),
        limit=_ALONG_LIMIT,
    )

    assert answer["items"], "the test line has services beside it in the dataset"
    assert {item["category"] for item in answer["items"]} <= services
    assert answer["measured"] == "distance_to_line"
    assert answer["not_measured"] == "detour_walking_time"
    assert answer["detour_confirmed"] is False


def test_any_named_category_is_measured_along_the_line(db_repos) -> None:
    """A sight can be asked for by code, where the service-only filter returned nothing."""
    answer = db_repos.places.places_along(
        _SOVETSKAYA_LINE, categories=["музей"], limit=_ALONG_LIMIT
    )

    assert answer["items"], "the test line has museums beside it in the dataset"
    assert {item["category"] for item in answer["items"]} == {"музей"}

    old = db_repos.places.services_along(
        _SOVETSKAYA_LINE,
        categories=["музей"],
        service_category_codes=_service_codes(db_repos),
        limit=_ALONG_LIMIT,
    )
    assert old["items"] == []
    assert old["reason"] == "no_service_categories"


def test_role_service_is_not_the_only_filter(db_repos) -> None:
    """The same mixed request finds both halves; the old filter kept only one."""
    both = db_repos.places.places_along(
        _SOVETSKAYA_LINE, categories=["кафе", "музей"], limit=_ALONG_LIMIT
    )
    categories = {item["category"] for item in both["items"]}

    assert {"кафе", "музей"} <= categories

    services_only = db_repos.places.services_along(
        _SOVETSKAYA_LINE,
        categories=["кафе", "музей"],
        service_category_codes=_service_codes(db_repos),
        limit=_ALONG_LIMIT,
    )
    assert {item["category"] for item in services_only["items"]} == {"кафе"}

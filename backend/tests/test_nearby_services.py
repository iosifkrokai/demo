"""The detour is measured, not guessed — that is the whole point of the tool."""

from __future__ import annotations

import pytest

OLD_TOWN = (53.6778, 23.8295)


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

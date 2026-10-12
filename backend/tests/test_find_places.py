"""Retrieval plumbing, without a model.

The embedder is mocked (see ``conftest._mocked_embedder``), so what these hold is
the wiring: which signals fire, how their rankings combine, what a card carries,
and that a bad request is refused rather than answered. Whether the *ranking* is
any good is a different question, asked by the quality harness against the live
model — not something a unit test can answer with a fake vector.
"""

from __future__ import annotations


def test_a_description_returns_usable_cards(db_repos) -> None:
    from agent.tools.places import find_places

    answer = find_places("кафедральный костёл", repos=db_repos, limit=5)

    assert answer.get("error") is None, answer.get("message")
    assert answer["count"] >= 1
    assert "embedding" in answer["provenance"]["signals"]
    for card in answer["results"]:
        # The id is the only handle the agent may use afterwards, so every card
        # must carry one, with a name to reason about.
        assert isinstance(card["id"], int)
        assert card["name"]
        assert card["lat"] and card["lon"]


def test_a_point_ranks_what_is_around_it(db_repos) -> None:
    from agent.tools.places import find_places

    answer = find_places(
        "", repos=db_repos, limit=5, near_lat=53.6778, near_lon=23.8295, radius_m=800
    )

    assert answer.get("error") is None, answer.get("message")
    assert answer["count"] >= 1
    assert "nearby" in answer["provenance"]["signals"]


def test_a_category_alone_is_enough_to_search(db_repos) -> None:
    from agent.tools.places import find_places

    answer = find_places("", ["музей"], repos=db_repos, limit=3)

    assert answer.get("error") is None, answer.get("message")
    assert answer["count"] >= 1
    assert "category" in answer["provenance"]["signals"]


def test_an_empty_request_is_an_error_not_a_flood(db_repos) -> None:
    from agent.tools.places import find_places

    answer = find_places("", repos=db_repos)
    assert answer["error"] == "empty_query"
    assert answer["results"] == []


def test_unknown_category_codes_are_dropped_not_passed_through(db_repos) -> None:
    from agent.tools.places import find_places

    answer = find_places("музей", ["музей", "выдуманная-категория"], repos=db_repos, limit=3)

    assert answer["provenance"]["dropped_codes"] == ["выдуманная-категория"]
    assert answer["provenance"]["categories"]  # the known code was resolved


def test_a_region_box_scopes_the_search(db_repos) -> None:
    """A territory box keeps every returned card inside it."""
    from agent.tools.places import find_places

    box = [53.66, 23.81, 53.70, 23.85]  # a small box around the Old Town
    answer = find_places("костёл", repos=db_repos, limit=5, region_bbox=box)

    assert answer.get("error") is None, answer.get("message")
    assert answer["provenance"]["region_bbox"] == box
    for card in answer["results"]:
        assert box[0] <= card["lat"] <= box[2]
        assert box[1] <= card["lon"] <= box[3]


def test_a_box_with_nothing_in_it_returns_nothing(db_repos) -> None:
    from agent.tools.places import find_places

    answer = find_places("костёл", repos=db_repos, limit=5, region_bbox=[0.0, 0.0, 1.0, 1.0])

    assert answer.get("error") is None, answer.get("message")
    assert answer["results"] == []


def test_one_area_scoped_search_yields_enough_stops_to_plan(db_repos) -> None:
    """The prompt tells the model one area-scoped search is the whole candidate set.

    That instruction is only honest if it is true: a single `find_places` call for
    the Old Town must hand back enough real stops to build a walk in the next call.
    If it did not, "search once" would starve the plan rather than speed it up.
    """
    from agent.tools.places import find_places
    from agent.tools.plan import plan_walk

    found = find_places("достопримечательности", repos=db_repos, limit=8, area="grodno-old-town")
    assert found.get("error") is None, found.get("message")
    ids = [card["id"] for card in found["results"]]
    assert len(ids) >= 3, "one search did not yield enough stops to choose a walk"

    walk = plan_walk(ids, repos=db_repos, budget_minutes=120)
    assert walk.get("error") is None, walk.get("message")
    assert walk["count"] >= 2


def test_the_project_area_has_a_boundary(db_repos) -> None:
    """The one area the dataset carries a polygon for resolves to a real box."""
    box = db_repos.areas.bbox("grodno-oblast")
    if box is None:
        import pytest

        pytest.skip("the project area has no boundary in this database")
    south, west, north, east = box
    assert south < north and west < east

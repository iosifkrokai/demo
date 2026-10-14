"""Retrieval plumbing, without a model."""

from __future__ import annotations


def test_a_description_returns_usable_cards(db_repos) -> None:
    from agent.tools.places import find_places

    answer = find_places("кафедральный костёл", repos=db_repos, limit=5)

    assert answer.get("error") is None, answer.get("message")
    assert answer["count"] >= 1
    assert "embedding" in answer["provenance"]["signals"]
    for card in answer["results"]:
        assert isinstance(card["id"], int)
        assert card["name"]
        assert card["lat"]
        assert card["lon"]


def test_an_alias_reaches_the_place_that_answers_to_it(db_repos) -> None:
    """«Парк Мир» is not the castle's name and no vector signal brings it in — the
    alias table is the only path from the query to «Мирский замок».
    """
    from agent.tools.places import find_places

    answer = find_places("Парк Мир", repos=db_repos, limit=8)

    assert answer.get("error") is None, answer.get("message")
    assert "alias" in answer["provenance"]["signals"]
    names = {card["name"] for card in answer["results"]}
    assert "Мирский замок" in names, names


def test_the_alias_signal_reaches_a_cross_script_name(db_repos) -> None:
    """The Belarusian form the request may be typed in finds the Russian row."""
    from agent.tools.places import find_places

    answer = find_places("Мірскі замак", repos=db_repos, limit=8)

    assert "alias" in answer["provenance"]["signals"]
    names = {card["name"] for card in answer["results"]}
    assert "Мирский замок" in names, names


def test_a_query_with_no_alias_keeps_the_old_signals(db_repos) -> None:
    """A search with no words asks the alias table nothing and says nothing about it."""
    from agent.tools.places import find_places

    answer = find_places("", ["музей"], repos=db_repos, limit=3)

    assert answer.get("error") is None, answer.get("message")
    assert "alias" not in answer["provenance"]["signals"]
    assert "category" in answer["provenance"]["signals"]


def test_a_point_ranks_what_is_around_it(db_repos) -> None:
    from agent.tools.places import Where, find_places

    answer = find_places(
        "",
        repos=db_repos,
        limit=5,
        where=Where(near_lat=53.6778, near_lon=23.8295, radius_m=800),
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
    assert answer["provenance"]["categories"]


def test_a_region_box_scopes_the_search(db_repos) -> None:
    """A territory box keeps every returned card inside it."""
    from agent.tools.places import find_places

    box = [53.66, 23.81, 53.70, 23.85]
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


def test_an_area_scoped_search_boxes_itself_to_the_territory(db_repos) -> None:
    """The area decides the box every signal is asked for.

    It does NOT narrow much today: `areas.json` gives a polygon to the oblast alone,
    so an area without a boundary is projected with none and the box stays the
    territory's — the ranking is the only thing keeping the answer local.
    """

    from agent.tools.places import Where, find_places

    found = find_places(
        "",
        ["памятник", "замок", "храм", "музей", "архитектура"],
        repos=db_repos,
        limit=8,
        where=Where(area="grodno-old-town"),
    )

    assert found.get("error") is None, found.get("message")
    assert found["provenance"]["area"] == "grodno-old-town"
    assert len(found["results"]) >= 3, "one search did not yield a set to choose from"


def test_a_search_scoped_by_radius_ranks_around_the_point(db_repos) -> None:
    """A point is a signal, not a fence: the other signals are free to win a place
    from anywhere in the territory, so this asserts the ranking ran, not a distance.
    """
    from agent.tools.places import Where, find_places

    found = find_places(
        "",
        ["памятник", "замок", "храм", "музей", "архитектура"],
        repos=db_repos,
        limit=8,
        where=Where(near_lat=53.6778, near_lon=23.8295, radius_m=1500),
    )

    assert found.get("error") is None, found.get("message")
    assert "nearby" in found["provenance"]["signals"]
    assert found["results"], "a search around a dense old town returns nothing"


def test_the_project_area_has_a_boundary(db_repos) -> None:
    """The one area the dataset carries a polygon for resolves to a real box."""
    box = db_repos.areas.bbox("grodno-oblast")
    if box is None:
        import pytest

        pytest.skip("the project area has no boundary in this database")
    south, west, north, east = box
    assert south < north
    assert west < east

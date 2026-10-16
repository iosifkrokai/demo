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


def test_a_named_area_keeps_the_answer_inside_its_polygon(db_repos) -> None:
    """The area is a shape, not a box: every card the search hands back stands in it."""
    from agent.tools.places import Where, find_places

    area = "lida-district"
    if not db_repos.areas.has_boundary(area):
        import pytest

        pytest.skip(f"{area} has no boundary in this database")

    found = find_places("костёл", repos=db_repos, limit=5, where=Where(area=area))

    assert found.get("error") is None, found.get("message")
    assert found["provenance"]["area"] == area
    assert found["results"], "a district search returned nothing to choose from"
    ids = [card["id"] for card in found["results"]]
    assert db_repos.places.outside_area(ids, area) == []


def test_the_city_is_a_hole_in_the_district_around_it(db_repos) -> None:
    """`grodno-district` surrounds the city and must not hand a district search the
    city's own points — the one case a bounding box gets wrong."""
    from agent.tools.places import Where, find_places

    if not db_repos.areas.has_boundary("grodno-district"):
        import pytest

        pytest.skip("grodno-district has no boundary in this database")

    found = find_places("", ["музей"], repos=db_repos, limit=8, where=Where(area="grodno-district"))

    assert found.get("error") is None, found.get("message")
    towns = {card["town"] for card in found["results"]}
    assert "Гродно" not in towns, towns


def test_an_area_without_a_boundary_is_searched_unscoped_and_says_so(db_repos) -> None:
    """A territory the data gives no polygon to cannot fence the search: the tool says
    `no_boundary` rather than pretending the request was narrowed to a place it cannot
    find in its own registry."""
    from agent.tools.places import Where, find_places

    found = find_places("костёл", repos=db_repos, limit=5, where=Where(area="выдуманный-район"))

    assert found.get("error") is None, found.get("message")
    assert found["provenance"]["area_filter"] == "no_boundary"
    assert found["results"], "an unbound area search must still return the territory's points"


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


def test_every_area_the_file_gives_a_polygon_to_carries_one(db_repos) -> None:
    """A boundary the seed failed to build is a search that silently stops narrowing."""
    import json

    import pytest

    from core.paths import GEO_DIR

    declared = json.loads((GEO_DIR / "areas.json").read_text(encoding="utf-8"))["areas"]
    bounded = [area["slug"] for area in declared if area.get("geometry")]
    if not bounded or not db_repos.places.ping():
        pytest.skip("database not reachable")

    missing = [slug for slug in bounded if not db_repos.areas.has_boundary(slug)]

    assert missing == [], f"полигон не доехал до базы: {missing}"

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

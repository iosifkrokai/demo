"""A catalogue is a list to choose from: places, no line, and no invented numbers."""

from __future__ import annotations

from typing import Any

from agent.client import AgentOutcome
from agent.schema import PlanSubmission
from api.responses import route_response
from db.models.place import Place as PlaceRow
from routing.models import CategoryFacts, Requirement, TripRequirements

NAMES = {1: "Фарный костёл", 2: "Костёл Нахождения", 3: "Церковь Бориса и Глеба"}


def _row(pid: int) -> PlaceRow:
    return PlaceRow(id=pid, name=NAMES[pid], lat=53.0, lon=23.0, visit_minutes=20)


class _Places:
    def __init__(self, ids: list[int]) -> None:
        self._by_id = {pid: _row(pid) for pid in ids}

    def get_by_ids(self, ids: list[int]) -> list[PlaceRow]:
        return [self._by_id[pid] for pid in ids if pid in self._by_id]


class _Taxonomy:
    def all(self) -> list[Any]:
        return []


class _Repos:
    def __init__(self, ids: list[int]) -> None:
        self.places = _Places(ids)
        self.taxonomy = _Taxonomy()


def _catalogue(final: list[int], *, narrative: str = "") -> Any:
    outcome = AgentOutcome(
        submission=PlanSubmission(ordered_ids=list(final), narrative=narrative),
        requirements=TripRequirements(
            result_mode="catalogue",
            requirements=[Requirement(kind="interest", code="костёл")],
        ),
        measured={
            "status": "ready",
            "results": [{"id": pid, "name": NAMES[pid]} for pid in final],
            "verdicts": [{"kind": "interest", "code": "костёл", "status": "satisfied"}],
            "capped": False,
        },
    )
    return route_response(outcome, repos=_Repos([1, 2, 3]), facts=CategoryFacts.from_rows([]))


def test_a_catalogue_answers_with_the_places_and_no_walk() -> None:
    """There is no walk to measure, so there is no line, no budget and no minutes
    to be read as if somebody had walked them."""
    answer = _catalogue([1, 2, 3])

    assert [point.id for point in answer.points] == [1, 2, 3]
    assert answer.shape == {}
    assert answer.budget is None
    assert answer.summary.time_seconds is None
    assert answer.summary.length_km is None
    assert answer.result_mode == "catalogue"
    assert answer.debug["n_places"] == 3
    assert answer.debug["verdicts"][0]["status"] == "satisfied"


def test_a_catalogue_carries_the_codes_it_was_asked_for() -> None:
    answer = _catalogue([1, 2])

    assert answer.parsed.categories == ["костёл"]
    assert answer.requirements[0]["code"] == "костёл"


def test_a_catalogue_says_how_many_places_it_holds() -> None:
    """Nobody wrote prose for it, so the answer still says what it is."""
    answer = _catalogue([1, 2, 3])

    assert "3" in answer.explanation


def test_a_catalogue_with_the_agent_own_words_keeps_them() -> None:
    answer = _catalogue([1, 2], narrative="Костёлы области — выбирайте.")

    assert answer.explanation == "Костёлы области — выбирайте."


def test_an_empty_catalogue_is_an_answer_not_a_crash() -> None:
    """The mode is the contract, not the count: nothing found is still a catalogue."""
    answer = _catalogue([])

    assert answer.points == []
    assert answer.status == "ready"
    assert answer.result_mode == "catalogue"

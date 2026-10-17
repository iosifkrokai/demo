"""The instruction text, held to its promise."""

from __future__ import annotations

from agent.prompts import compose_instructions
from core.constants import MAX_PLACES_PER_FIND


def _instructions() -> str:
    return compose_instructions([], [], "")


def test_a_territory_search_is_bounded_to_one_call() -> None:
    """A territory is searched once, then planned, then submitted — by rule, not budget."""
    text = _instructions().lower()
    assert "search it once." in text
    assert "single find_places call" in text
    assert "plan_walk a single time" in text
    assert "do not search a territory a second time" in text


def test_the_one_search_rule_is_scoped_to_a_walk_not_a_catalogue() -> None:
    """'Search once, then plan_walk' must not swallow the catalogue outcome: a
    request for every instance of a category is still a list, not a walk."""
    text = _instructions()
    assert "separate `catalogue` outcome below" in text


def test_the_one_search_rule_names_the_cap_the_tool_enforces() -> None:
    """The prompt promises 'up to N real stops'; N is read from the tool, so the
    text cannot drift from what `find_places` actually returns."""
    text = _instructions()
    assert f"up to {MAX_PLACES_PER_FIND} real stops" in text


def test_the_prompt_names_the_tool_that_judges_a_requirement() -> None:
    """The verdict is the code's: the model is told which tool speaks it, and when."""
    text = _instructions().lower()
    assert "never decide whether a requirement is satisfied" in text
    assert "check_requirements" in text
    assert "before you submit" in text


def test_the_worked_old_town_shape_is_a_three_call_path() -> None:
    """The worked shape spells the short path out, so 'choose the stops yourself'
    does not read as a licence to search each sight in turn."""
    text = _instructions()
    assert 'find_places(where={"area": "grodno-old-town"})' in text
    assert "three calls" in text

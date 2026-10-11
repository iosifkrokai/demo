"""Offline trajectory: a ``FunctionModel`` replays an agent run with no provider.

The whole agent path runs — instructions, the tool loop, the structured output,
the mapping into ``TripRequirements`` — while ``fake_model`` stands in for
OpenRouter. The run is bounded by the script the test writes, so it is exact and
returns in milliseconds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.client import interpret_with_agent
from agent.models import ReaderBrief

if TYPE_CHECKING:
    from tests.conftest import FakeModel


def test_a_plain_reading_replays_without_a_provider(fake_model: FakeModel) -> None:
    """No tool call: one model turn, one structured reading, one merged contract."""
    fake_model.finish(children=2, budget_minutes=120)

    result = interpret_with_agent("два часа с двумя детьми", ReaderBrief(locale="ru"))

    assert result is not None
    assert result.party.children == 2
    assert result.budget_minutes == 120
    assert result.source == "llm"


def test_a_tool_call_is_answered_before_the_final_reading(fake_model: FakeModel) -> None:
    """The script plays turn by turn: the tool call first, the reading after it."""
    fake_model.call("find_places", query="замок").finish(budget_minutes=90)

    result = interpret_with_agent("замок на полтора часа", ReaderBrief(locale="ru"))

    assert result is not None
    assert result.budget_minutes == 90


def test_the_ui_brief_wins_over_the_reading(fake_model: FakeModel) -> None:
    """A visible control outranks the model, and the provenance says so."""
    fake_model.finish(budget_minutes=240, result_mode="catalogue")

    result = interpret_with_agent("погулять", ReaderBrief(locale="ru", time_budget_minutes=60))

    assert result is not None
    assert result.budget_minutes == 60
    assert result.source == "mixed"

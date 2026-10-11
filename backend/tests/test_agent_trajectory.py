"""Offline trajectory: a ``FunctionModel`` replays a planning run with no provider.

The whole path runs — instructions, the tool loop, the terminal submission, the
merge into a contract and the code's verdict on it — while ``fake_model`` stands
in for the provider. The run is bounded by the script the test writes, so it is
exact and returns in milliseconds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.client import plan_with_agent
from agent.models import ReaderBrief

if TYPE_CHECKING:
    from tests.conftest import FakeModel

OLD_TOWN = (53.6778, 23.8295)


def _ids(repos, limit: int = 4) -> list[int]:
    found = repos.places.nearby(OLD_TOWN[0], OLD_TOWN[1], radius_km=1.5, limit=limit)
    return [row.id for row in found if row.id is not None]


def test_a_submission_replays_without_a_provider(fake_model: FakeModel, db_repos) -> None:
    """One model turn, one submission, and the code's verdict on it."""
    ids = _ids(db_repos)
    fake_model.finish(ordered_ids=ids, budget_minutes=120, narrative="Прогулка по старому городу")

    outcome = plan_with_agent("два часа пешком", ReaderBrief(locale="ru"), repos=db_repos)

    assert outcome is not None
    assert outcome.submission.narrative == "Прогулка по старому городу"
    assert outcome.requirements.budget_minutes == 120
    # The status is code's: the run submitted, the tools answered.
    assert outcome.status in ("ready", "degraded", "infeasible", "pending")


def test_a_tool_call_is_answered_before_the_submission(fake_model: FakeModel, db_repos) -> None:
    """The script plays turn by turn: the tool call first, the submission after it."""
    ids = _ids(db_repos)
    fake_model.call("find_places", query="замок").finish(ordered_ids=ids, budget_minutes=90)

    outcome = plan_with_agent("замок на полтора часа", ReaderBrief(locale="ru"), repos=db_repos)

    assert outcome is not None
    assert outcome.requirements.budget_minutes == 90


def test_the_ui_brief_wins_over_the_submission(fake_model: FakeModel, db_repos) -> None:
    """A visible control outranks the model, and the provenance says so."""
    ids = _ids(db_repos)
    fake_model.finish(ordered_ids=ids, budget_minutes=240, result_mode="catalogue")

    outcome = plan_with_agent(
        "погулять", ReaderBrief(locale="ru", time_budget_minutes=60), repos=db_repos
    )

    assert outcome is not None
    assert outcome.requirements.budget_minutes == 60
    assert outcome.requirements.source == "mixed"


def test_a_run_that_never_submits_produces_no_plan(fake_model: FakeModel, db_repos) -> None:
    """No submission, no plan — the loop cannot end by trailing off."""
    fake_model.call("find_places", query="замок")

    outcome = plan_with_agent("замок", ReaderBrief(locale="ru"), repos=db_repos)

    assert outcome is None

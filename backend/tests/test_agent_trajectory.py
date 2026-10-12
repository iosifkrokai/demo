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
    fake_model.finish(ordered_ids=ids, budget_minutes=240, outcome="catalogue")

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


def test_a_refusal_is_an_infeasible_answer_not_a_tool_error(
    fake_model: FakeModel, db_repos
) -> None:
    """Out of coverage is expressible now: it leaves as infeasible with a reason."""
    fake_model.finish(
        outcome="refuse",
        refusal_reason="Вильнюс — за пределами обслуживаемого региона",
        outside_coverage=["Вильнюс"],
    )

    outcome = plan_with_agent("прогулка по Вильнюсу", ReaderBrief(locale="ru"), repos=db_repos)

    assert outcome is not None
    assert outcome.refused is True
    assert outcome.status == "infeasible"
    assert outcome.requirements.outside_coverage == ["Вильнюс"]
    assert outcome.problems and outcome.problems[0]["reason"]


def test_the_wire_status_of_a_refusal_is_infeasible(fake_model: FakeModel, db_repos) -> None:
    """The refusal reaches the response body as status="infeasible", not pending."""
    from api.models.routes import GenerateReq
    from api.responses import reader_brief, route_response
    from routing.models import CategoryFacts

    fake_model.finish(outcome="refuse", refusal_reason="Не наш регион")
    req = GenerateReq(query="прогулка по Вильнюсу")
    outcome = plan_with_agent(req.query, reader_brief(req), repos=db_repos)
    assert outcome is not None

    response = route_response(
        outcome, repos=db_repos, facts=CategoryFacts.from_rows(db_repos.taxonomy.all())
    )

    assert response.status == "infeasible"
    assert response.points == []
    assert response.explanation and "регион" in response.explanation


def test_an_empty_route_submission_is_not_a_plan(fake_model: FakeModel, db_repos) -> None:
    """A route with no stops is refused rather than answered as pending-with-none."""
    fake_model.finish(ordered_ids=[], outcome="route")

    outcome = plan_with_agent("Гродно за два часа", ReaderBrief(locale="ru"), repos=db_repos)

    assert outcome is None


def test_a_catalogue_outcome_selects_the_catalogue_mode(fake_model: FakeModel, db_repos) -> None:
    """route vs catalogue is read off the submission's outcome."""
    ids = _ids(db_repos)
    fake_model.finish(ordered_ids=ids, outcome="catalogue")

    outcome = plan_with_agent("все костёлы области", ReaderBrief(locale="ru"), repos=db_repos)

    assert outcome is not None
    assert outcome.requirements.result_mode == "catalogue"


def test_a_deleted_stop_is_subtracted_from_both_lists() -> None:
    """A hand-deleted stop is enforced in code, not only by the prompt note."""
    from agent.client import _without_excluded
    from agent.models import ReaderBrief, RefinementBrief

    brief = ReaderBrief(locale="ru", context=RefinementBrief(excluded_ids=(2,)))
    ordered, keep = _without_excluded([1, 2, 3], [2, 3], brief)

    assert ordered == [1, 3]
    assert keep == [3]


def test_the_run_carries_a_live_deadline_and_region(
    fake_model: FakeModel, db_repos, monkeypatch
) -> None:
    """`seconds_left()` stops being infinite: the deadline reaches the tools."""
    from agent import runner

    seen: dict[str, object] = {}
    real = runner._run_agent

    def spy(query, brief, deps, *, wall_clock_s=None):
        seen["left"] = deps.seconds_left()
        seen["bbox"] = deps.region_bbox
        return real(query, brief, deps, wall_clock_s=wall_clock_s)

    monkeypatch.setattr(runner, "_run_agent", spy)
    ids = _ids(db_repos)
    fake_model.finish(ordered_ids=ids, budget_minutes=120)

    outcome = plan_with_agent(
        "два часа пешком",
        ReaderBrief(locale="ru", region_bbox=(53.6, 23.7, 53.75, 23.9)),
        repos=db_repos,
        wall_clock_s=10,
    )

    assert outcome is not None
    assert 0 < seen["left"] <= 10  # type: ignore[operator]
    assert seen["bbox"] == (53.6, 23.7, 53.75, 23.9)


def test_the_wall_clock_is_capped_by_the_ceiling() -> None:
    from agent.runner import WALL_CLOCK_TIMEOUT_S, effective_wall_clock

    assert effective_wall_clock(None) == WALL_CLOCK_TIMEOUT_S
    assert effective_wall_clock(0) == WALL_CLOCK_TIMEOUT_S
    assert effective_wall_clock(10_000) == WALL_CLOCK_TIMEOUT_S
    assert effective_wall_clock(40) == 40


def test_time_short_appends_a_submit_nudge_once() -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from agent.runner import SUBMIT_NUDGE, _submission_nudge

    base = [ModelRequest([UserPromptPart("plan this")])]
    assert _submission_nudge(base, 999) is base

    nudged = _submission_nudge(base, 5)
    assert nudged is not base
    assert any(
        isinstance(p, UserPromptPart) and p.content == SUBMIT_NUDGE for p in nudged[-1].parts
    )
    # A second call with the same short time does not stack the reminder.
    assert _submission_nudge(nudged, 5) is nudged

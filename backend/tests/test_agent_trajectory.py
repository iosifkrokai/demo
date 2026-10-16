"""Offline trajectory: a ``FunctionModel`` replays a planning run with no provider."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic_ai.messages import ToolReturnPart

from agent import runner
from agent.client import plan_with_agent
from agent.models import ReaderBrief
from agent.tools import ERR_TOOL_BUDGET
from core import constants
from routing.models import CategoryFacts

if TYPE_CHECKING:
    from tests.conftest import FakeModel, LiveStack

OLD_TOWN = (53.6778, 23.8295)

NEARBY_LIMIT = 200


def _ids(repos, limit: int = 4) -> list[int]:
    found = repos.places.nearby(OLD_TOWN[0], OLD_TOWN[1], radius_km=1.5, limit=limit)
    return [row.id for row in found if row.id is not None]


def _nearby(repos) -> list[Any]:
    return repos.places.nearby(OLD_TOWN[0], OLD_TOWN[1], radius_km=1.2, limit=NEARBY_LIMIT)


def _sights(repos, limit: int = 3) -> list[int]:
    """Real sights, not services: the walk of a request that names a territory."""
    facts = CategoryFacts.from_rows(repos.taxonomy.all())
    rows = [row for row in _nearby(repos) if not facts.is_service(row.category or "")]
    return [row.id for row in rows[:limit] if row.id is not None]


def _service(repos, category: str) -> int:
    """One real place of a service category near the old town."""
    for row in _nearby(repos):
        if (row.category or "") == category and row.id is not None:
            return int(row.id)
    pytest.fail(f"no {category} near the old town in this database")


def _spy(seen: list[str | None]):
    """A `_run_agent` that records the repair note each pass was handed."""
    real = runner._run_agent

    def spy(query, brief, deps, *, wall_clock_s=None, repair=None):
        seen.append(repair)
        return real(query, brief, deps, wall_clock_s=wall_clock_s, repair=repair)

    return spy


def test_a_submission_replays_without_a_provider(fake_model: FakeModel, db_repos) -> None:
    """One model turn, one submission, and the code's verdict on it."""
    ids = _ids(db_repos)
    fake_model.finish(ordered_ids=ids, budget_minutes=120, narrative="Прогулка по старому городу")

    outcome = plan_with_agent("два часа пешком", ReaderBrief(locale="ru"), repos=db_repos)

    assert outcome is not None
    assert outcome.submission.narrative == "Прогулка по старому городу"
    assert outcome.requirements.budget_minutes == 120
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


def test_the_wheelchair_control_reaches_the_measurement(
    fake_model: FakeModel, db_repos, monkeypatch
) -> None:
    """The ticked control travels the whole way to the request the engine measures."""
    from agent.tools import submit as submit_mod
    from routing.models import WalkRequest

    handed: list[WalkRequest] = []
    real = submit_mod.measure_submission

    def spy(request: WalkRequest, submission: Any, **kwargs: Any) -> Any:
        handed.append(request)
        return real(request, submission, **kwargs)

    monkeypatch.setattr(submit_mod, "measure_submission", spy)
    fake_model.finish(ordered_ids=_ids(db_repos), budget_minutes=120)

    outcome = plan_with_agent(
        "два часа пешком", ReaderBrief(locale="ru", mobility=("wheelchair",)), repos=db_repos
    )

    assert outcome is not None
    assert handed[0].step_free is True


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
    assert outcome.problems
    assert outcome.problems[0]["reason"]


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
    assert response.explanation
    assert "регион" in response.explanation


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
    left: list[float] = []
    bbox: list[tuple[float, float, float, float] | None] = []
    real = runner._run_agent

    def spy(query, brief, deps, *, wall_clock_s=None, repair=None):
        left.append(deps.seconds_left())
        bbox.append(deps.region_bbox)
        return real(query, brief, deps, wall_clock_s=wall_clock_s, repair=repair)

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
    assert 0 < left[0] <= 10
    assert bbox[0] == (53.6, 23.7, 53.75, 23.9)


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
    assert _submission_nudge(nudged, 5) is nudged


def test_a_route_that_breaks_a_hard_rule_is_rebuilt_once(
    fake_model: FakeModel, db_repos, monkeypatch, live_stack: LiveStack
) -> None:
    """An exclusion the walk breaks is fixed in one more pass, not shipped as it is."""
    live_stack.require("valhalla")
    sights = _sights(db_repos)
    cafe = _service(db_repos, "кафе")
    seen: list[str | None] = []
    monkeypatch.setattr(runner, "_run_agent", _spy(seen))
    fake_model.finish(ordered_ids=[*sights, cafe], must_keep_ids=[cafe], budget_minutes=240)
    fake_model.finish(ordered_ids=sights, budget_minutes=240)
    brief = ReaderBrief(locale="ru", avoid=("кафе",))

    outcome = plan_with_agent("два часа пешком, без кафе", brief, repos=db_repos)

    assert outcome is not None
    assert outcome.status == "ready"
    assert [repair is None for repair in seen] == [True, False]
    assert "avoid_violated" in (seen[1] or "")


def test_a_route_that_breaks_nothing_is_not_rebuilt(
    fake_model: FakeModel, db_repos, monkeypatch
) -> None:
    """Nothing to repair: the model is asked once, and the second step stays unspent."""
    seen: list[str | None] = []
    monkeypatch.setattr(runner, "_run_agent", _spy(seen))
    fake_model.finish(ordered_ids=_sights(db_repos), budget_minutes=240)

    outcome = plan_with_agent("два часа пешком", ReaderBrief(locale="ru"), repos=db_repos)

    assert outcome is not None
    assert seen == [None]


def test_a_refusal_is_never_rebuilt(fake_model: FakeModel, db_repos, monkeypatch) -> None:
    """Outside the region is an answer, not a bad plan: it gets no second pass."""
    seen: list[str | None] = []
    monkeypatch.setattr(runner, "_run_agent", _spy(seen))
    fake_model.finish(outcome="refuse", refusal_reason="Вильнюс — вне региона")

    outcome = plan_with_agent("прогулка по Вильнюсу", ReaderBrief(locale="ru"), repos=db_repos)

    assert outcome is not None
    assert outcome.refused is True
    assert seen == [None]


def test_a_second_try_that_helps_nothing_is_dropped(
    fake_model: FakeModel, db_repos, monkeypatch
) -> None:
    """Both passes break the same rule: what the tourist gets is the first one."""
    sights = _sights(db_repos)
    cafe = _service(db_repos, "кафе")
    seen: list[str | None] = []
    monkeypatch.setattr(runner, "_run_agent", _spy(seen))
    fake_model.finish(
        ordered_ids=[*sights, cafe], must_keep_ids=[cafe], budget_minutes=240, narrative="первый"
    )
    fake_model.finish(
        ordered_ids=[*sights, cafe], must_keep_ids=[cafe], budget_minutes=240, narrative="второй"
    )
    brief = ReaderBrief(locale="ru", avoid=("кафе",))

    outcome = plan_with_agent("два часа пешком, без кафе", brief, repos=db_repos)

    assert outcome is not None
    assert len(seen) == 2
    assert outcome.submission.narrative == "первый"


def test_an_oversized_turn_is_trimmed_instead_of_ending_the_pass(
    fake_model: FakeModel, db_repos, monkeypatch
) -> None:
    """Three calls in one turn against a budget of two: the surplus is refused.

    A provider may put several calls in one answer, and the framework's own limit
    projects the whole batch before anything runs — so the pass used to be thrown
    away and the budget note asked the model to spend it all over again.
    """
    monkeypatch.setattr(constants, "MAX_TOOL_CALLS", 2)
    ids = _ids(db_repos)
    seen: list[str | None] = []
    monkeypatch.setattr(runner, "_run_agent", _spy(seen))
    fake_model.turn(
        ("find_places", {"query": "замок"}),
        ("find_places", {"query": "костёл"}),
        ("find_places", {"query": "музей"}),
    ).finish(ordered_ids=ids, budget_minutes=120)

    outcome = plan_with_agent("два часа пешком", ReaderBrief(locale="ru"), repos=db_repos)

    assert outcome is not None
    assert seen == [None], "the first pass answered — it was not spent again"
    refusals = [
        part.content
        for messages in fake_model.seen
        for message in messages
        for part in getattr(message, "parts", ())
        if isinstance(part, ToolReturnPart)
        and isinstance(part.content, dict)
        and part.content.get("error") == ERR_TOOL_BUDGET
    ]
    assert len(refusals) == 1, "the third call is the one that went over the budget"


def test_a_call_the_model_got_wrong_does_not_spend_the_budget(
    fake_model: FakeModel, db_repos, monkeypatch
) -> None:
    """Two good calls and one the schema rejects, against a budget of two.

    A call that never reaches a tool body is not a call the model spent, so the
    pass still finishes with a submission instead of being cut short.
    """
    monkeypatch.setattr(constants, "MAX_TOOL_CALLS", 2)
    ids = _ids(db_repos)
    seen: list[str | None] = []
    monkeypatch.setattr(runner, "_run_agent", _spy(seen))
    fake_model.turn(
        ("find_places", {}),
        ("find_places", {"query": "замок"}),
        ("find_places", {"query": "костёл"}),
    ).finish(ordered_ids=ids, budget_minutes=120)

    outcome = plan_with_agent("два часа пешком", ReaderBrief(locale="ru"), repos=db_repos)

    assert outcome is not None
    assert seen == [None]


def test_the_ids_a_tool_returned_are_recorded(fake_model: FakeModel, db_repos, monkeypatch) -> None:
    """The id guard reads what a tool actually answered — and it is a hook now."""
    handed: list[Any] = []
    real = runner._run_agent

    def spy(query, brief, deps, *, wall_clock_s=None, repair=None):
        handed.append(deps)
        return real(query, brief, deps, wall_clock_s=wall_clock_s, repair=repair)

    monkeypatch.setattr(runner, "_run_agent", spy)
    ids = _ids(db_repos)
    fake_model.call("find_places", query="замок").finish(ordered_ids=ids, budget_minutes=120)

    outcome = plan_with_agent("два часа пешком", ReaderBrief(locale="ru"), repos=db_repos)

    assert outcome is not None
    assert handed[0].observed_ids, "the run saw places, so it must have recorded them"

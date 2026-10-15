"""The one repair attempt: when it runs, what it says, and which try wins."""

from __future__ import annotations

import time
from typing import Any

import pytest

from agent import client, runner
from agent.client import (
    AgentOutcome,
    _better,
    _budget_text,
    _rank,
    _refused,
    _remaining_s,
    _repair_note,
)
from agent.models import ReaderBrief, TripRequirements
from agent.runner import _build_prompt
from agent.schema import PlanningDeps, PlanSubmission
from core import constants
from routing.models import Requirement
from routing.verify import REASON_GEOMETRY_MISSING, REASON_HARD_SERVICE_ABSENT


def _failed_service(reason: str = REASON_HARD_SERVICE_ABSENT) -> Requirement:
    return Requirement(
        kind="service",
        strength="hard",
        code="туалет",
        status="unmet",
        reason=reason,
        text="уборная — обязательно",
    )


def _outcome(
    *,
    status: str,
    requirements: list[Requirement] | None = None,
    problems: list[dict[str, str]] | None = None,
    results: list[dict[str, Any]] | None = None,
    fits_budget: bool | None = True,
    error: str | None = None,
) -> AgentOutcome:
    return AgentOutcome(
        submission=PlanSubmission(),
        requirements=TripRequirements(requirements=list(requirements or [])),
        measured={
            "status": status,
            "problems": list(problems or []),
            "results": list(results or []),
            "fits_budget": fits_budget,
            "error": error,
        },
    )


def _problem(reason: str = REASON_HARD_SERVICE_ABSENT) -> dict[str, str]:
    return {"kind": "service", "code": "туалет", "status": "unmet", "reason": reason}


def _refusal() -> AgentOutcome:
    submission = PlanSubmission(outcome="refuse", refusal_reason="Вильнюс — вне региона")
    return AgentOutcome(
        submission=submission, requirements=TripRequirements(), measured=_refused(submission)
    )


def _deps(left_s: float) -> PlanningDeps:
    return PlanningDeps(deadline_at=time.monotonic() + left_s)


def test_a_ready_route_outranks_a_degraded_one() -> None:
    ready = _outcome(status="ready", results=[{"id": 4}])
    degraded = _outcome(status="degraded", problems=[_problem()], results=[{"id": 4}])

    assert _rank(ready.measured) < _rank(degraded.measured)


def test_a_route_inside_the_budget_outranks_one_outside_it() -> None:
    inside = _outcome(status="ready", fits_budget=True)
    outside = _outcome(status="ready", fits_budget=False)

    assert _rank(inside.measured) < _rank(outside.measured)


def test_fewer_hard_problems_win() -> None:
    one = _outcome(status="degraded", problems=[_problem()])
    two = _outcome(status="degraded", problems=[_problem(), _problem()])

    assert _rank(one.measured) < _rank(two.measured)


def test_a_tie_keeps_the_first_try() -> None:
    first = _outcome(status="degraded", problems=[_problem()], results=[{"id": 4}])
    second = _outcome(status="degraded", problems=[_problem()], results=[{"id": 4}])

    assert _better(first, second) is first


def test_a_refused_second_try_never_replaces_a_route() -> None:
    route = _outcome(status="degraded", problems=[_problem()], results=[{"id": 4}])

    assert _better(route, _refusal()) is route


def test_a_repairable_failure_is_handed_back_to_the_model() -> None:
    outcome = _outcome(status="degraded", requirements=[_failed_service()], problems=[_problem()])

    note = _repair_note(outcome, _deps(120))

    assert note is not None
    assert REASON_HARD_SERVICE_ABSENT in note
    assert "туалет" in note
    assert "уборная — обязательно" in note


def test_an_unfixable_failure_is_not_what_the_second_try_is_asked_to_fix() -> None:
    outcome = _outcome(
        status="degraded",
        requirements=[_failed_service(REASON_GEOMETRY_MISSING)],
        problems=[_problem(REASON_GEOMETRY_MISSING)],
    )

    assert _repair_note(outcome, _deps(120)) is None


def test_a_route_outside_the_budget_is_rebuilt_by_itself() -> None:
    outcome = _outcome(status="ready", fits_budget=False)

    note = _repair_note(outcome, _deps(120))

    assert note is not None
    assert "budget" in note


def test_a_ready_route_is_never_rebuilt() -> None:
    assert _repair_note(_outcome(status="ready"), _deps(120)) is None


def test_a_catalogue_is_never_rebuilt() -> None:
    outcome = _outcome(status="degraded", requirements=[_failed_service()], problems=[_problem()])
    outcome.requirements.result_mode = "catalogue"

    assert _repair_note(outcome, _deps(120)) is None


def test_a_lost_measurement_is_never_rebuilt() -> None:
    outcome = _outcome(
        status="pending",
        requirements=[_failed_service()],
        problems=[_problem()],
        error="no_route",
    )

    assert _repair_note(outcome, _deps(120)) is None


def test_a_refusal_is_never_rebuilt() -> None:
    assert _repair_note(_refusal(), _deps(120)) is None


def test_a_run_that_is_nearly_out_of_time_is_not_rebuilt() -> None:
    """A refinement gets `REFINE_DEADLINE_S`, which is below the repair floor."""
    outcome = _outcome(status="degraded", requirements=[_failed_service()], problems=[_problem()])

    assert constants.REFINE_DEADLINE_S < constants.REPAIR_MIN_LEFT_S
    assert _repair_note(outcome, _deps(constants.REFINE_DEADLINE_S)) is None


def test_the_second_try_gets_what_is_left_not_a_fresh_budget() -> None:
    assert _remaining_s(_deps(12), 150) == pytest.approx(12, abs=1)


def test_a_run_without_a_deadline_keeps_the_caller_budget() -> None:
    assert _remaining_s(PlanningDeps(), 90) == 90


def test_the_repair_note_is_appended_to_the_prompt() -> None:
    brief = ReaderBrief(locale="ru")

    base = _build_prompt("два часа пешком", brief)

    assert _build_prompt("два часа пешком", brief, repair="NOTE") == base + "\nNOTE"


def _planner(monkeypatch, script: list[tuple[AgentOutcome | None, str | None]]) -> list[str | None]:
    """A `plan_with_agent` whose runs answer from a script, recording each note."""
    notes: list[str | None] = []

    def fake_attempt(query, brief, deps, wall_clock_s, *, repair):
        notes.append(repair)
        return script[min(len(notes), len(script)) - 1]

    monkeypatch.setattr(client, "available", lambda: True)
    monkeypatch.setattr(client, "_deps", lambda *a, **k: _deps(120))
    monkeypatch.setattr(client, "_attempt", fake_attempt)
    return notes


def test_a_run_that_spent_its_tool_budget_is_tried_again(monkeypatch) -> None:
    """Being cut off at the twelfth call is not a reason to answer 503."""
    ready = _outcome(status="ready", results=[{"id": 4}])
    notes = _planner(monkeypatch, [(None, runner.TOOL_BUDGET_FAILURE), (ready, None)])

    outcome = client.plan_with_agent("музеи Гродно", ReaderBrief())

    assert outcome is ready
    assert len(notes) == 2
    assert notes[1] == _budget_text()


def test_another_failure_is_not_tried_again(monkeypatch) -> None:
    notes = _planner(monkeypatch, [(None, "timeout"), (None, None)])

    assert client.plan_with_agent("музеи Гродно", ReaderBrief()) is None
    assert len(notes) == 1


def test_a_second_budget_failure_still_answers_nothing(monkeypatch) -> None:
    notes = _planner(
        monkeypatch, [(None, runner.TOOL_BUDGET_FAILURE), (None, runner.TOOL_BUDGET_FAILURE)]
    )

    assert client.plan_with_agent("музеи Гродно", ReaderBrief()) is None
    assert len(notes) == 2


def test_a_run_nearly_out_of_time_is_not_spent_on_a_second_pass(monkeypatch) -> None:
    """The wall clock is the caller's: a pass that has 3 s left cannot plan anything."""
    notes: list[str | None] = []

    def fake_attempt(query, brief, deps, wall_clock_s, *, repair):
        notes.append(repair)
        return None, runner.TOOL_BUDGET_FAILURE

    monkeypatch.setattr(client, "available", lambda: True)
    monkeypatch.setattr(client, "_attempt", fake_attempt)
    monkeypatch.setattr(client, "_deps", lambda *a, **k: _deps(2))

    assert client.plan_with_agent("музеи Гродно", ReaderBrief()) is None
    assert len(notes) == 1


def test_the_budget_note_names_the_budget_and_says_where_to_stop() -> None:
    text = _budget_text()

    assert str(constants.MAX_TOOL_CALLS) in text
    assert "unknowns" in text


def test_the_budget_failure_name_is_the_one_the_library_raises() -> None:
    """The retry hangs on that string, so a rename in the SDK must fail here."""
    pytest.importorskip("pydantic_ai.exceptions")
    from pydantic_ai.exceptions import UsageLimitExceeded

    raised = UsageLimitExceeded.__name__

    assert raised == runner.TOOL_BUDGET_FAILURE

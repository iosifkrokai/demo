"""The one repair attempt: when it runs, what it says, and which try wins."""

from __future__ import annotations

import time
from typing import Any

import pytest

from agent.client import AgentOutcome, _better, _rank, _refused, _remaining_s, _repair_note
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

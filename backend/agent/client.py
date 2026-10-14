"""The planning agent's public entry point."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent import llm, mapping, runner
from agent.models import ReaderBrief, TripRequirements
from agent.schema import PlanningDeps, PlanSubmission
from agent.tools import submit as submit_mod
from core import constants
from routing.models import LatLon, Requirement, WalkRequest
from routing.verify import INFEASIBLE_REASONS, REPAIRABLE_REASONS

if TYPE_CHECKING:
    from db.store.registry import Repositories

log = logging.getLogger(__name__)

__all__ = ["AgentOutcome", "available", "plan_with_agent"]


@dataclass(frozen=True)
class AgentOutcome:
    """One run's whole result: what was proposed, and what the code proved."""

    submission: PlanSubmission
    requirements: TripRequirements
    measured: dict[str, Any]

    @property
    def status(self) -> str:
        return str(self.measured.get("status") or "pending")

    @property
    def problems(self) -> list[dict[str, str]]:
        return list(self.measured.get("problems") or [])

    @property
    def refused(self) -> bool:
        """True when the agent refused outright (outside coverage, not a bad plan)."""
        return self.submission.outcome == "refuse"


def available() -> bool:
    """True when this layer could run: a key, a model name, an importable SDK."""
    if not llm.chosen_provider().api_key:
        return False
    return bool(llm.model_name())


def plan_with_agent(
    query: str,
    brief: ReaderBrief,
    *,
    repos: Repositories | None = None,
    wall_clock_s: float | None = None,
    on_repair: Callable[[], None] | None = None,
) -> AgentOutcome | None:
    """Returns a measured, verified outcome, or ``None``."""
    text = query.strip()
    if not text:
        return None
    if not available():
        log.info("agent_plan: agent unavailable (no key/model) — no planner")
        runner._record_model_failure("agent_unavailable", runner._build_prompt(text, brief))
        return None

    deps = _deps(brief, repos, wall_clock_s)
    first = _attempt(text, brief, deps, wall_clock_s, repair=None)
    if first is None or first.refused:
        return first

    note = _repair_note(first, deps)
    if note is None:
        return first
    if on_repair is not None:
        on_repair()
    second = _attempt(text, brief, deps, _remaining_s(deps, wall_clock_s), repair=note)
    if second is None:
        return first
    return _better(first, second)


def _attempt(
    query: str,
    brief: ReaderBrief,
    deps: PlanningDeps,
    wall_clock_s: float | None,
    *,
    repair: str | None,
) -> AgentOutcome | None:
    """One whole try: the model's answer, its contract, and the code's measurement."""
    submission = _submission(query, brief, deps, wall_clock_s, repair=repair)
    if submission is None:
        return None
    contract = mapping._merge(query, brief, submission, deps.observed_ids, deps.repos)
    if submission.outcome == "refuse":
        return AgentOutcome(
            submission=submission, requirements=contract, measured=_refused(submission)
        )
    return _measured(brief, deps, submission, contract)


def _remaining_s(deps: PlanningDeps, wall_clock_s: float | None) -> float | None:
    """What is left of the run's budget, so a second try cannot outlive the first."""
    if deps.deadline_at is None:
        return wall_clock_s
    return max(0.0, deps.seconds_left())


def _repair_note(outcome: AgentOutcome, deps: PlanningDeps) -> str | None:
    """Tell the model what the code could not prove, when it is the model's to fix."""
    if outcome.requirements.result_mode != "route" or outcome.measured.get("error") is not None:
        return None
    failed = [
        item
        for item in outcome.requirements.hard()
        if item.status in ("unmet", "uncertain") and item.reason in REPAIRABLE_REASONS
    ]
    fits = outcome.measured.get("fits_budget")
    if not failed and fits is not False:
        return None
    if deps.seconds_left() < constants.REPAIR_MIN_LEFT_S:
        return None
    log.info(
        "agent_plan: one repair attempt (%s, %.0fs left)",
        ", ".join(item.reason or item.kind for item in failed) or "over_budget",
        deps.seconds_left(),
    )
    return _repair_text(failed, outcome.measured, fits is False)


def _repair_text(failed: list[Requirement], measured: dict[str, Any], over_budget: bool) -> str:
    """The note handed to the second try: the failure, the route it came from, the levers."""
    lines = ["Your previous plan was measured, and the code could not prove it:"]
    lines += [_failed_line(item) for item in failed]
    if over_budget:
        lines.append("- the walk does not fit the time budget the request gave")
    stops = [row for row in (measured.get("results") or []) if isinstance(row, dict)]
    shown = "; ".join(f"{row.get('id')}:{row.get('name')}" for row in stops[:20])
    if shown:
        lines.append(f"Previous stops: {shown}.")
    lines += [
        "Plan again so that every hard requirement above can be proven:",
        "- a place you need goes on the route, pinned in must_keep_ids;",
        "- a service or an interest the request names needs a stop of that category;",
        "- a stop that breaks an exclusion has to go.",
        "Call check_requirements with the stops you are about to submit, then submit.",
    ]
    return "\n".join(lines)


def _failed_line(item: Requirement) -> str:
    """One failed requirement as the model reads it: what was asked and why it failed."""
    asked = item.code or item.name or item.kind
    said = f" («{item.text}»)" if item.text else ""
    return f"- {item.kind} {asked}{said}: {item.status} ({item.reason})"


_STATUS_RANK = {"ready": 0, "degraded": 1, "pending": 2, "infeasible": 3}


def _rank(measured: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """How good a measurement is, best first: the code's verdict, then its own numbers."""
    status = str(measured.get("status") or "pending")
    problems = list(measured.get("problems") or [])
    return (
        _STATUS_RANK.get(status, 2),
        1 if measured.get("fits_budget") is False else 0,
        len(problems),
        sum(1 for problem in problems if problem.get("reason") in INFEASIBLE_REASONS),
        -len(measured.get("results") or []),
    )


def _better(first: AgentOutcome, second: AgentOutcome) -> AgentOutcome:
    """The better of two tries; a tie keeps the first, so a repair never loses ground."""
    return first if _rank(first.measured) <= _rank(second.measured) else second


def _deps(
    brief: ReaderBrief, repos: Repositories | None, wall_clock_s: float | None
) -> PlanningDeps:
    """The run's reach: what it may read, and when its clock runs out."""
    deps = PlanningDeps(
        repos=repos,
        locale=brief.locale,
        origin=_origin(brief),
        region_bbox=brief.region_bbox,
        deadline_at=time.monotonic() + runner.effective_wall_clock(wall_clock_s),
    )
    if repos is not None:
        deps.facts = type(deps.facts).from_rows(repos.taxonomy.all())
    return deps


def _submission(
    query: str,
    brief: ReaderBrief,
    deps: PlanningDeps,
    wall_clock_s: float | None,
    *,
    repair: str | None = None,
) -> PlanSubmission | None:
    """The model's answer, or None with the reason already logged."""
    try:
        submission, failure = runner._run_agent(
            query, brief, deps, wall_clock_s=wall_clock_s, repair=repair
        )
    except Exception as exc:
        log.warning("agent_plan: unexpected failure: %s: %s", type(exc).__name__, exc)
        return None
    if submission is None:
        log.info("agent_plan: no usable submission (%s) — no plan", failure)
    return submission


def _measured(
    brief: ReaderBrief, deps: PlanningDeps, submission: PlanSubmission, contract: TripRequirements
) -> AgentOutcome | None:
    """The measured, verified outcome of a submission the model actually made."""
    ordered, keep = _with_base_stops(submission, brief)
    ordered, keep = _without_excluded(ordered, keep, brief)
    if contract.result_mode == "route" and not ordered:
        log.info("agent_plan: empty route submission — no plan")
        return None

    measured = submit_mod.submit_plan(
        WalkRequest(
            stop_ids=ordered,
            profile=contract.costing or "pedestrian",
            budget_minutes=contract.budget_minutes,
            origin=_origin(brief),
            round_trip=submission.round_trip or brief.round_trip,
            must_keep_ids=keep,
            locale=brief.locale,
        ),
        submission,
        repos=deps.repos,
        facts=deps.facts,
        contract=contract,
        deadline_s=deps.seconds_left(),
    )
    return AgentOutcome(submission=submission, requirements=contract, measured=measured)


def _refused(submission: PlanSubmission) -> dict[str, Any]:
    """The measurement of a refusal: infeasible, no stops, and the reason attached."""
    reason = (submission.refusal_reason or "").strip()
    return {
        "status": "infeasible",
        "ok": False,
        "results": [],
        "shape": None,
        "verdicts": [],
        "provenance": {"source": "agent", "refused": True},
        "problems": [{"kind": "request", "code": "refused", "status": "unmet", "reason": reason}],
    }


def _without_excluded(
    ordered: list[int], keep: list[int], brief: ReaderBrief
) -> tuple[list[int], list[int]]:
    """Drop the stops the user deleted by hand from both the route and the keeps."""
    excluded = set(brief.context.excluded_ids) if brief.context else set()
    if not excluded:
        return ordered, keep
    return (
        [i for i in ordered if i not in excluded],
        [i for i in keep if i not in excluded],
    )


def _with_base_stops(submission: PlanSubmission, brief: ReaderBrief) -> tuple[list[int], list[int]]:
    """Keep the previous route's stops, whatever the model proposed."""
    base = [
        point.place_id
        for point in (brief.context.base_points if brief.context else ())
        if point.place_id is not None
    ]
    keep = list(dict.fromkeys([*submission.must_keep_ids, *base]))
    ordered = list(dict.fromkeys([*submission.ordered_ids, *base]))
    return ordered, keep


def _origin(brief: ReaderBrief) -> LatLon | None:
    """The tourist's position as the model the routing layer expects."""
    if brief.origin is None:
        return None
    return LatLon(lat=brief.origin[0], lon=brief.origin[1])

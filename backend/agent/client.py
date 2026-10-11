"""The planning agent's public entry point.

``available()`` answers "is the agent configured"; ``plan_with_agent`` is the only
way in. Everything else in the package is an implementation detail.

A run does not *claim* a plan is good. It submits one, and this module hands the
submission straight back to the deterministic tools — re-measured, re-verified —
so what leaves here carries a status code computed the status, not narrated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent import llm, mapping, runner
from agent.models import ReaderBrief, TripRequirements
from agent.schema import PlanningDeps, PlanSubmission
from agent.tools import submit as submit_mod

if TYPE_CHECKING:
    from db.store.registry import Repositories

log = logging.getLogger(__name__)

__all__ = ["AgentOutcome", "available", "plan_with_agent"]


@dataclass(frozen=True)
class AgentOutcome:
    """One run's whole result: what was proposed, and what the code proved.

    ``measured`` is the verifier's answer — `status`, `ok`, `problems`, the
    verdict list and the arithmetic — and it is never the model's own summary.
    """

    submission: PlanSubmission
    requirements: TripRequirements
    measured: dict[str, Any]

    @property
    def status(self) -> str:
        return str(self.measured.get("status") or "pending")

    @property
    def problems(self) -> list[dict[str, str]]:
        return list(self.measured.get("problems") or [])


def available() -> bool:
    """True when this layer could run: a key, a model name, an importable SDK.

    Cheap and offline — it answers "is the agent configured", not "is the provider up".
    """
    if not llm.api_key():
        return False
    return bool(llm.model_name())


def plan_with_agent(
    query: str,
    brief: ReaderBrief,
    *,
    repos: Repositories | None = None,
    wall_clock_s: float | None = None,
) -> AgentOutcome | None:
    """Returns a measured, verified outcome, or ``None``.

    ``None`` is never a half-filled plan: it means the run did not produce one,
    and the caller refuses the request rather than guessing at the query.
    """
    if not query.strip():
        return None
    if not available():
        log.info("agent_plan: agent unavailable (no key/model) — no planner")
        runner._record_model_failure("agent_unavailable", runner._build_prompt(query, brief))
        return None

    deps = PlanningDeps(repos=repos, locale=brief.locale, origin=_origin(brief))
    if repos is not None:
        deps.facts = type(deps.facts).from_rows(repos.taxonomy.all())

    try:
        submission, failure = runner._run_agent(
            query.strip(), brief, deps, wall_clock_s=wall_clock_s
        )
    except Exception as exc:
        log.warning("agent_plan: unexpected failure: %s: %s", type(exc).__name__, exc)
        return None
    if submission is None:
        log.info("agent_plan: no usable submission (%s) — no plan", failure)
        return None

    contract = mapping._merge(query.strip(), brief, submission, deps.observed_ids, deps.repos)
    ordered, keep = _with_base_stops(submission, brief)
    measured = submit_mod.submit_plan(
        ordered,
        repos=repos,
        facts=deps.facts,
        budget_minutes=contract.budget_minutes,
        profile=submission.profile,
        origin=_origin(brief),
        round_trip=submission.round_trip or brief.round_trip,
        must_keep_ids=keep,
        locale=brief.locale,
        contract=contract,
        unknowns=submission.unknowns,
        narrative=submission.narrative,
        result_mode=submission.result_mode or brief.result_mode,
        deadline_s=deps.seconds_left(),
    )
    return AgentOutcome(submission=submission, requirements=contract, measured=measured)


def _with_base_stops(submission: PlanSubmission, brief: ReaderBrief) -> tuple[list[int], list[int]]:
    """Keep the previous route's stops, whatever the model proposed.

    A refinement is a delta on a route the tourist already holds, so a stop that
    survives it must survive the proposal too — that is what used to make a
    refinement turn unable to answer 422.
    """
    base = [
        point.place_id
        for point in (brief.context.base_points if brief.context else ())
        if point.place_id is not None
    ]
    keep = list(dict.fromkeys([*submission.must_keep_ids, *base]))
    ordered = list(dict.fromkeys([*submission.ordered_ids, *base]))
    return ordered, keep


def _origin(brief: ReaderBrief):
    """The tourist's position as the model the routing layer expects."""
    if brief.origin is None:
        return None
    from routing.models import LatLon

    return LatLon(lat=brief.origin[0], lon=brief.origin[1])

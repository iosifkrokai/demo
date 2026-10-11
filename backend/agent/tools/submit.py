"""``submit_plan`` — the end of the run, and the only way one ends.

The model does not get to declare its own work good. Whatever is submitted is
re-measured and re-verified here, by the same code the agent may have called
while planning, and the answer carries a status that came from that code. The
tool is a schema rather than free text, so a submission cannot arrive half-shaped.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from agent.schema import PlanningDeps
from agent.tools import ERR_BAD_ARGUMENT, Remember, ToolAnswer, envelope
from agent.tools.plan import plan_walk
from agent.tools.verify import requirements_from, verdicts_of
from routing.models import LatLon
from routing.stops import candidate_of
from routing.verify import verify, verify_catalogue

RESULT_MODES = ("route", "catalogue")


def _problems(verdicts: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The hard requirements the code could not prove — what a `ready` cannot survive."""
    return [
        {
            "kind": item.get("kind") or "",
            "code": item.get("code") or "",
            "status": item.get("status") or "",
            "reason": item.get("detail") or "",
        }
        for item in verdicts
        if item.get("strength") == "hard" and item.get("status") in ("unmet", "uncertain")
    ]


def submit_plan(
    ordered_ids: list[int],
    *,
    repos: Any,
    facts: Any,
    budget_minutes: int | None = None,
    profile: str = "pedestrian",
    origin: LatLon | None = None,
    round_trip: bool = False,
    must_keep_ids: list[int] | None = None,
    locale: str = "ru",
    requirement_specs: list[dict[str, Any]] | None = None,
    unknowns: list[str] | None = None,
    narrative: str | None = None,
    result_mode: str = "route",
    deadline_s: float | None = None,
) -> dict[str, Any]:
    """Measure and verify the finished plan; the status is code's, not the model's."""
    provenance: dict[str, Any] = {
        "source": "places+valhalla",
        "result_mode": result_mode,
        "fact_status": "re-measured on submit",
    }
    if result_mode not in RESULT_MODES:
        return envelope(
            "submit_plan",
            [],
            provenance,
            error=ERR_BAD_ARGUMENT,
            message=f"result_mode must be one of {RESULT_MODES}",
        )

    contract = requirements_from(requirement_specs)
    contract.unknowns = list(unknowns or [])

    if result_mode == "catalogue":
        rows = repos.places.get_by_ids(ordered_ids or [])
        places = [candidate_of(row, 0.0) for row in rows]
        verify_catalogue(contract, places, facts=facts)
        answer = verdicts_of(contract)
        return envelope("submit_plan", answer["verdicts"], provenance) | {
            "ok": answer["status"] == "ready",
            "problems": _problems(answer["verdicts"]),
            "narrative": narrative,
            **answer,
        }

    measured = plan_walk(
        ordered_ids,
        repos=repos,
        budget_minutes=budget_minutes,
        profile=profile,
        origin=origin,
        round_trip=round_trip,
        must_keep_ids=must_keep_ids,
        locale=locale,
        facts=facts,
        deadline_s=deadline_s,
    )
    if measured.get("error") is not None:
        provenance["reason_code"] = measured.get("error")
        return envelope(
            "submit_plan",
            [],
            provenance,
            error=measured.get("error"),
            message=measured.get("message"),
        )

    stops = [candidate_of(row, 0.0) for row in repos.places.get_by_ids(ordered_ids)]
    verify(contract, {"route": stops, "trace": {}}, measured.get("shape"), facts=facts)
    answer = verdicts_of(contract)

    return envelope(
        "submit_plan",
        measured["results"],
        provenance,
        capped=measured.get("capped", False),
    ) | {
        "ok": answer["status"] == "ready",
        "problems": _problems(answer["verdicts"]),
        "narrative": narrative,
        "walk_minutes": measured["walk_minutes"],
        "visit_minutes": measured["visit_minutes"],
        "total_minutes": measured["total_minutes"],
        "fits_budget": measured["fits_budget"],
        "shape": measured.get("shape"),
        **answer,
    }


def register_submit_plan(agent: Any, remember: Remember) -> None:
    """Advertise ``submit_plan`` to the agent — the terminal call."""

    @agent.tool(name="submit_plan")
    def _submit_plan(
        ctx: RunContext[PlanningDeps],
        ordered_ids: list[int],
        requirement_specs: list[dict[str, Any]],
        narrative: str,
        budget_minutes: int | None = None,
        profile: str = "pedestrian",
        round_trip: bool = False,
        must_keep_ids: list[int] | None = None,
        unknowns: list[str] | None = None,
        result_mode: str = "route",
    ) -> ToolAnswer:
        """Submit the finished plan. Code re-measures it and returns the verdicts.

        Call this once, at the end. `ok` is true only when code proved every hard
        requirement satisfied; `problems` names what it could not prove, and the
        `narrative` you send is kept as your own words, not as the verdict.
        """
        answer = submit_plan(
            ordered_ids,
            repos=ctx.deps.repos,
            facts=ctx.deps.facts,
            budget_minutes=budget_minutes,
            profile=profile,
            origin=ctx.deps.origin,
            round_trip=round_trip,
            must_keep_ids=must_keep_ids,
            locale=ctx.deps.locale,
            requirement_specs=requirement_specs,
            unknowns=unknowns,
            narrative=narrative,
            result_mode=result_mode,
            deadline_s=ctx.deps.seconds_left(),
        )
        return remember(ctx, answer)

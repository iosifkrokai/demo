"""``check_requirements`` — the deterministic verdict on the current route."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic_ai import RunContext

from agent.schema import PlanningDeps
from agent.tools import ERR_BAD_ARGUMENT, Remember, ToolAnswer, envelope, fetch
from routing.models import Requirement, TripRequirements
from routing.stops import candidate_of
from routing.verify import overall_status, steps_evidence, verify, verify_summary

MAX_SPECS_PER_CALL = 20


def requirements_from(specs: list[dict[str, Any]] | None) -> TripRequirements:
    """The agent's requirement specs as the contract the verifier grades."""
    requirement_list = [Requirement(**spec) for spec in (specs or [])[:MAX_SPECS_PER_CALL]]
    return TripRequirements(requirements=requirement_list)


def verdicts_of(contract: TripRequirements) -> dict[str, Any]:
    """The contract as the answer every verdict-returning tool gives."""
    return {
        "status": overall_status(contract),
        "summary": verify_summary(contract),
        "verdicts": contract.public_requirements(),
        "unknowns": list(contract.unknowns),
    }


def check_requirements(  # noqa: PLR0913 — the tool's whole surface, all four of them
    requirement_specs: list[dict[str, Any]],
    stop_ids: list[int],
    *,
    repos: Any,
    facts: Any,
    geometry: dict[str, Any] | None = None,
    unknowns: list[str] | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Report which requirements the given stops provably satisfy, and why not."""
    provenance: dict[str, Any] = {
        "source": "places",
        "result_cap": MAX_SPECS_PER_CALL,
        "stops": len(stop_ids or []),
    }
    if not stop_ids:
        return envelope(
            "check_requirements",
            [],
            provenance,
            error=ERR_BAD_ARGUMENT,
            message="there is no route to check: give the stop ids first",
        )

    rows, error, message = fetch(repos, lambda store: store.places.get_by_ids(stop_ids))
    if error is not None:
        return envelope("check_requirements", [], provenance, error=error, message=message)

    contract = requirements_from(requirement_specs)
    contract.unknowns = list(unknowns or [])
    stops = [candidate_of(row, 0.0) for row in (rows or [])]
    steps = steps_evidence(repos, geometry) if contract.of_kind("step_free") else None
    verify(contract, {"route": stops, "trace": {}}, geometry, facts=facts, at=at, steps_along=steps)

    answer = verdicts_of(contract)
    return envelope("check_requirements", answer["verdicts"], provenance) | answer


def register_check_requirements(agent: Any, _remember: Remember) -> None:
    """Advertise ``check_requirements`` to the agent."""

    @agent.tool(name="check_requirements")
    def _check_requirements(
        ctx: RunContext[PlanningDeps],
        requirement_specs: list[dict[str, Any]],
        stop_ids: list[int],
        geometry: dict[str, Any] | None = None,
        unknowns: list[str] | None = None,
    ) -> ToolAnswer:
        """Re-read the route by code and report what it provably satisfies."""
        return check_requirements(
            requirement_specs,
            stop_ids,
            repos=ctx.deps.repos,
            facts=ctx.deps.facts,
            geometry=geometry,
            unknowns=unknowns,
            at=ctx.deps.now,
        )

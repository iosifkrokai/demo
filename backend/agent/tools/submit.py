"""``submit_plan`` — the end of the run, and the only way one ends."""

from __future__ import annotations

from typing import Any

from agent.schema import PlanSubmission
from agent.tools import envelope
from agent.tools.places import card_of, catalogue_rows
from agent.tools.plan import plan_walk
from agent.tools.verify import verdicts_of
from routing.models import TripRequirements, WalkRequest
from routing.stops import candidate_of
from routing.verify import ServiceAlongEvidence, verify, verify_catalogue

MAX_SERVICES_MEASURED = 24


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


def _evidence(repos: Any, contract: TripRequirements, shape: Any) -> ServiceAlongEvidence | None:
    """Measure the services beside the line, for the codes the request named."""
    codes = _named_codes(contract)
    if not codes or not shape or repos is None:
        return None
    try:
        answer = repos.places.services_along(
            shape,
            categories=codes,
            service_category_codes=repos.taxonomy.service_codes(),
            limit=MAX_SERVICES_MEASURED,
        )
    except Exception:
        return ServiceAlongEvidence(measured=False, by_code={})
    by_code: dict[str, list[dict[str, Any]]] = {}
    for item in answer.get("items", []):
        by_code.setdefault(str(item.get("category")), []).append(item)
    return ServiceAlongEvidence(measured=True, by_code=by_code)


def _named_codes(contract: TripRequirements) -> list[str]:
    """Every category code the request named, services included."""
    return sorted(
        {r.code for r in contract.requirements if r.kind in ("service", "interest") and r.code}
    )


def _catalogue_codes(contract: TripRequirements) -> list[str]:
    """What a catalogue is of: every code the request named, minus what it forbade."""
    forbidden = set(contract.avoid_codes())
    return [code for code in _named_codes(contract) if code not in forbidden]


def submit_plan(
    request: WalkRequest,
    submission: PlanSubmission,
    *,
    repos: Any,
    facts: Any,
    contract: TripRequirements,
    deadline_s: float | None = None,
) -> dict[str, Any]:
    """Measure and verify the finished plan; the status is code's, not the model's.

    The verdicts are read off the route that was measured, not off the ids that were
    asked for: the planner drops stops — a budget too small for the walk, a point
    nothing reaches, a service that is not a stop — and a requirement proved by a stop
    the walk does not contain would be a satisfied nobody can see.
    """
    result_mode = contract.result_mode
    narrative = submission.narrative
    provenance: dict[str, Any] = {
        "source": "places+valhalla",
        "result_mode": result_mode,
        "fact_status": "re-measured on submit",
    }
    contract.unknowns = list(submission.unknowns)

    if result_mode == "catalogue":
        rows, capped = catalogue_rows(
            list(request.stop_ids),
            categories=_catalogue_codes(contract),
            area=contract.areas[0] if contract.areas else None,
            repos=repos,
        )
        places = [candidate_of(row, 0.0) for row in rows]
        verify_catalogue(contract, places, facts=facts, at=contract.at)
        answer = verdicts_of(contract)
        provenance["capped"] = capped
        return envelope(
            "submit_plan", [card_of(row) for row in rows], provenance, capped=capped
        ) | {
            "ok": answer["status"] == "ready",
            "problems": _problems(answer["verdicts"]),
            "narrative": narrative,
            **answer,
        }

    measured = plan_walk(request, repos=repos, facts=facts, deadline_s=deadline_s)
    if measured.get("error") is not None:
        provenance["reason_code"] = measured.get("error")
        return envelope(
            "submit_plan",
            [],
            provenance,
            error=measured.get("error"),
            message=measured.get("message"),
        )

    stops = list(measured.get("results") or ())
    shape = measured.get("shape")
    verify(
        contract,
        {"route": stops, "trace": {}},
        shape,
        _evidence(repos, contract, shape),
        facts=facts,
        at=contract.at,
    )
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
        "length_km": measured.get("length_km"),
        "visit_minutes": measured["visit_minutes"],
        "total_minutes": measured["total_minutes"],
        "fits_budget": measured["fits_budget"],
        "shape": measured.get("shape"),
        **answer,
    }

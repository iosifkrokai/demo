"""Catalogue response: the matching places grouped by town, and no route."""

from __future__ import annotations

import logging
import time as _time
from typing import Any

from contracts.planner import (
    Candidate,
    GenerateReq,
    ParsedQuery,
    ResolvedConstraints,
    RouteResponse,
    RouteSummary,
)
from domain import constants
from infra import progress, trace

from .response import _interpretation, _to_places, _verdicts
from .verify import overall_status, verify_catalogue

log = logging.getLogger(__name__)


def catalogue_response(
    req: GenerateReq,
    requirements: Any,
    intent: Any,
    constraints: ResolvedConstraints,
    candidates: list[Candidate],
    t0: float,
) -> RouteResponse:
    """Answer with the matching places, grouped by town, and no route.

    Ordered by town then by relevance, so the order is stable between runs.
    """
    ordered = sorted(candidates, key=lambda c: ((c.town or "").strip().lower(), -c.relevance))
    verify_catalogue(requirements, ordered)
    status = overall_status(requirements)
    progress.note(progress.STAGE_DONE)

    towns = sorted({(c.town or "").strip() for c in ordered if (c.town or "").strip()})
    log.info(
        "pipeline.catalogue query_len=%d n=%d towns=%d ms=%d status=%s",
        len(req.query), len(ordered), len(towns),
        int((_time.perf_counter() - t0) * 1000), status,
    )
    trace.record(
        "verify",
        input=[r.code or r.text for r in requirements.requirements][:10],
        plan_status=status,
        output=_verdicts(requirements),
    )
    trace.record(
        "response",
        input={"plan_status": status, "places": len(ordered)},
        plan_status=status,
        places=len(ordered),
        towns=len(towns),
        ms=int((_time.perf_counter() - t0) * 1000),
        names=[c.name for c in ordered],
    )

    d = intent.decision
    return RouteResponse(
        parsed=ParsedQuery(
            keywords=d.keywords_pos,
            categories=d.categories_pos,
            time_budget_minutes=d.time_budget_minutes,
            source=intent.source,
        ),
        points=_to_places(ordered),
        shape={},
        summary=RouteSummary(length_km=None, time_seconds=None),
        result_mode="catalogue",
        budget=None,
        explanation=(
            f"Каталог: {len(ordered)} мест"
            + (f" в {len(towns)} городах" if len(towns) > 1 else "")
            + " — выберите точки, и я построю по ним маршрут."
        ),
        status=status,
        requirements=requirements.public_requirements(),
        interpretation=_interpretation(requirements, status),
        costing=req.profile,
        changes=None,
        debug={
            "result_mode": "catalogue",
            "n_places": len(ordered),
            "towns": towns,
            "requirements_source": getattr(requirements, "source", None),
            "deadline": {
                "budget_s": constants.REQUEST_DEADLINE_S,
                "used_s": round(_time.perf_counter() - t0, 3),
                "pool_trimmed_to": None,
                "valhalla_order_skipped": True,
                "geometry_skipped": True,
            },
        },
    )

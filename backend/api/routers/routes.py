"""HTTP surface for planning: generate, reroute and explain.

The routes are thin; the decisions live in ``planner.pipeline.Pipeline``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from api import deps
from core.errors import AgentError, NoRoutePossibleError
from planner.models import ExplainReq, GenerateReq, RerouteReq, RouteResponse
from planner.response import no_route_response
from telemetry import progress, trace

router = APIRouter(tags=["routes"])


@router.post("/routes/generate", response_model=RouteResponse)
def generate(req: GenerateReq, request: Request) -> RouteResponse:
    """Build a route. With a `progress_id`, the work is reported as it happens."""
    deps.require_llm()
    progress.begin(req.progress_id)
    trace.begin(req.progress_id, session_id=req.session_id)
    try:
        try:
            return deps.get_planner(request).generate(req=req)
        except NoRoutePossibleError as exc:
            trace.record(
                "response",
                input={"query": req.query, "budget_minutes": req.time_budget_minutes},
                plan_status="infeasible",
                reason="no_route_possible",
            )
            return no_route_response(req, str(exc))
        except AgentError as exc:
            trace.record(
                "response",
                "error",
                input={"query": req.query, "budget_minutes": req.time_budget_minutes},
                plan_status="error",
                reason=type(exc).__name__,
                http_status=exc.http_status,
            )
            raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    finally:
        progress.finish()
        trace.finish()


@router.post("/routes/reroute", response_model=RouteResponse)
def reroute(req: RerouteReq, request: Request) -> RouteResponse:
    deps.require_llm()
    return deps.call_planner(
        deps.get_planner(request).reroute, point_ids=req.point_ids, profile=req.profile
    )


@router.post("/routes/explain")
def explain(req: ExplainReq, request: Request) -> dict[str, Any]:
    deps.require_llm()
    return {
        "explanation": deps.call_planner(
            deps.get_planner(request).explain_route, point_ids=req.point_ids
        )
    }

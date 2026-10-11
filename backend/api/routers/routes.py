"""HTTP surface for planning: generate, reroute, explain and the telemetry reads.

The routes are thin; the decisions live in ``planner.pipeline.Pipeline``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api import deps
from core.errors import AgentError, NoRoutePossible
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
        except NoRoutePossible as exc:
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


@router.get("/routes/trace/{trace_id}")
def route_trace(trace_id: str) -> dict:
    """The structured spans of one run, for a client that wants them raw.

    An unknown id is a 404 with a reason code, not an empty trace.
    """
    data = trace.get(trace_id)
    if data is None:
        raise deps.http_error(404, "unknown_trace_id")
    return data


@router.get("/routes/progress/{progress_id}")
def route_progress(progress_id: str) -> dict:
    """Where the pipeline got to, in stage codes the client localises.

    An unknown id is a 404 with a reason code, not an empty stage.
    """
    snapshot = progress.snapshot(progress_id)
    if snapshot is None:
        raise deps.http_error(404, "unknown_progress_id")
    return snapshot


@router.post("/routes/reroute", response_model=RouteResponse)
def reroute(req: RerouteReq, request: Request) -> RouteResponse:
    deps.require_llm()
    return deps.call_planner(
        deps.get_planner(request).reroute, point_ids=req.point_ids, profile=req.profile
    )


@router.post("/routes/explain")
def explain(req: ExplainReq, request: Request) -> dict:
    deps.require_llm()
    return {
        "explanation": deps.call_planner(
            deps.get_planner(request).explain_route, point_ids=req.point_ids
        )
    }

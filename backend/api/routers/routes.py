"""HTTP surface for planning: generate, reroute and explain.

The routes are thin. Everything that decides anything happens in the agent's
tools; this layer narrows the request, runs the agent, and writes down the answer.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agent import client, thread
from api import deps, responses
from api.models.routes import ExplainReq, GenerateReq, RerouteReq, RouteResponse
from core import constants
from core.errors import AgentError, InterpretationUnavailableError, NoRoutePossibleError
from routing.models import CategoryFacts
from routing.stops import candidate_of

log = logging.getLogger(__name__)

router = APIRouter(tags=["routes"])

NO_PLAN = "no_plan"


@router.post("/routes/generate", response_model=RouteResponse)
def generate(req: GenerateReq, request: Request) -> RouteResponse:
    """Build a route. With a `progress_id`, the work is reported as it happens."""
    deps.require_llm()
    started = time.perf_counter()
    try:
        answer = _plan(req, request)
    except NoRoutePossibleError as exc:
        log.info("no route possible: %s", exc)
        return responses.no_route_response(req, str(exc))
    except AgentError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    log.info(
        "plan.ok query_len=%d ms=%d stops=%d status=%s",
        len(req.query),
        int((time.perf_counter() - started) * 1000),
        len(answer.points),
        answer.status,
    )
    return answer


def _plan(req: GenerateReq, request: Request) -> RouteResponse:
    """One agent run, then the code's verdict on what it proposed."""
    repos = deps.get_repos(request)
    brief = responses.reader_brief(req)
    if req.session_id:
        # The thread the client names is the journal the refinement reconciles
        # against; the snapshot it re-sends is a fallback, not the authority.
        brief = thread.reconcile_brief(repos.threads, thread_id=req.session_id, brief=brief)
    # Read the refinement off the reconciled brief, not the raw body: the journal
    # can add base points, and the plan below is built from the brief.
    # A refinement gets its own tight budget; a fresh generation gets none here —
    # the runner's own ceiling is the single source for that wall clock.
    refinement = bool(brief.context and brief.context.base_points)
    outcome = client.plan_with_agent(
        req.query,
        brief,
        repos=repos,
        wall_clock_s=(constants.REFINE_DEADLINE_S if refinement else None),
    )
    if outcome is None:
        # No plan is not an empty plan: the caller is refused rather than given
        # a guess at what the tourist meant.
        raise InterpretationUnavailableError("the request could not be planned")

    if req.session_id:
        thread.record(
            repos.threads,
            thread_id=req.session_id,
            query=req.query,
            submission=outcome.submission.model_dump(mode="json"),
            measurement=outcome.measured,
        )

    facts = CategoryFacts.from_rows(repos.taxonomy.all())
    return responses.route_response(req, outcome, repos=repos, facts=facts)


@router.post("/routes/reroute", response_model=RouteResponse)
def reroute(req: RerouteReq, request: Request) -> RouteResponse:
    """Route the points the client already holds. No model, no prompt."""
    deps.require_llm()
    return deps.guarded(responses.reroute_response, req=req, repos=deps.get_repos(request))


@router.post("/routes/explain")
def explain(req: ExplainReq, request: Request) -> dict[str, Any]:
    """A short description of a list of points, written from their own data."""
    deps.require_llm()
    repos = deps.get_repos(request)
    rows = repos.places.get_by_ids(req.point_ids)
    if len(rows) != len(req.point_ids):
        raise deps.http_error(404, "unknown_point_ids")
    facts = CategoryFacts.from_rows(repos.taxonomy.all())
    candidates = [candidate_of(row, 0.0) for row in rows]
    return {"explanation": responses.explain_points(candidates, facts)}

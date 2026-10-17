"""HTTP surface for planning: generate, reroute and explain."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from agent import client, thread
from agent.models import BriefStop
from api import deps, responses
from api.models.routes import (
    ExplainReq,
    GenerateReq,
    RerouteReq,
    RouteProgressOut,
    RouteResponse,
    ThreadResponse,
    ThreadTurn,
)
from core import constants, progress
from core.errors import AgentError, InterpretationUnavailableError, NoRoutePossibleError
from core.passwords import hash_token
from db.store.users import UserRepository
from routing.models import CategoryFacts
from routing.stops import candidate_of

if TYPE_CHECKING:
    from db.models.thread import Thread

log = logging.getLogger(__name__)

router = APIRouter(tags=["routes"])


def _identity(request: Request) -> str | None:
    """The namespace a run belongs to: the hash of the session that named it."""
    token = deps.session_token(request)
    return hash_token(token) if token else None


def _thread_response(session_id: str, journal: Thread | None) -> ThreadResponse:
    """The journal as the wire model — the turns, never the rows themselves."""
    if journal is None:
        return ThreadResponse(session_id=session_id)
    return ThreadResponse(
        session_id=session_id,
        created_at=journal.created_at,
        turns=[
            ThreadTurn(
                revision=turn.revision,
                query=turn.query,
                submission=turn.submission,
                measurement=turn.measurement,
                created_at=turn.created_at,
            )
            for turn in journal.turns
        ],
    )


@router.post("/routes/generate", response_model=RouteResponse)
def generate(req: GenerateReq, request: Request) -> RouteResponse:
    """Build a route. With a `progress_id`, the work is reported as it happens."""
    deps.require_llm()
    progress.RUNS.begin(req.progress_id)
    started = time.perf_counter()
    try:
        try:
            answer = _plan(req, request)
        except NoRoutePossibleError as exc:
            log.info("no route possible: %s", exc)
            answer = responses.no_route_response(req, str(exc))
        except AgentError as exc:
            raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except Exception:
        progress.RUNS.finish(req.progress_id, failed=True)
        raise
    progress.RUNS.finish(req.progress_id)
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
    identity = _identity(request)
    thread_id = thread.thread_key(req.session_id, identity=identity) if req.session_id else None
    previous: tuple[BriefStop, ...] = ()
    if brief.context is not None:
        brief, previous = thread.reconcile_context(
            repos.threads, thread_id=thread_id, brief=brief, identity=identity
        )
    refinement = bool(brief.context and brief.context.base_points)
    outcome = client.plan_with_agent(
        req.query,
        brief,
        repos=repos,
        wall_clock_s=(constants.REFINE_DEADLINE_S if refinement else None),
        on_repair=lambda: progress.RUNS.mark(req.progress_id, progress.REPAIRING_ROUTE),
    )
    if outcome is None:
        raise InterpretationUnavailableError("the request could not be planned")

    if thread_id:
        thread.record(
            repos.threads,
            thread_id=thread_id,
            query=req.query,
            submission=outcome.submission.model_dump(mode="json"),
            measurement=outcome.measured,
        )

    facts = CategoryFacts.from_rows(repos.taxonomy.all())
    progress.RUNS.mark(req.progress_id, progress.DRAWING_LINE)
    return responses.route_response(outcome, repos=repos, facts=facts, previous=previous)


@router.get("/routes/progress/{progress_id}", response_model=RouteProgressOut)
def read_progress(progress_id: str) -> Any:
    """How far the run named by `progress_id` got; 404 for an id nobody is tracking."""
    state = progress.RUNS.read(progress_id)
    if state is None:
        return deps.error(404, "unknown_progress_id")
    return RouteProgressOut(**state)


@router.get("/routes/threads/{session_id}", response_model=ThreadResponse)
@deps.storage_guarded
def read_thread(
    session_id: str,
    request: Request,
    repo: Annotated[UserRepository, Depends(deps.get_user_repository)],
) -> Any:
    """The turns recorded for this run, read back by the session that named it."""
    _user, err = deps.require_user(request, repo)
    if err is not None:
        return err
    thread_id = thread.thread_key(session_id, identity=_identity(request))
    journal = deps.get_repos(request).threads.journal(thread_id)
    return _thread_response(session_id, journal)


@router.post("/routes/reroute", response_model=RouteResponse)
def reroute(req: RerouteReq, request: Request) -> RouteResponse:
    """Measure and order the points the client already holds. No model, no prompt."""
    try:
        return responses.reroute_response(req=req, repos=deps.get_repos(request))
    except AgentError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc


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

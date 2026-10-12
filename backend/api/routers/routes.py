"""HTTP surface for planning: generate, reroute and explain.

The routes are thin. Everything that decides anything happens in the agent's
tools; this layer narrows the request, runs the agent, and writes down the answer.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request

from agent import client, thread
from agent.models import BriefStop
from api import deps, responses
from api.models.routes import (
    ExplainReq,
    GenerateReq,
    RerouteReq,
    RouteResponse,
    ThreadResponse,
    ThreadTurn,
)
from core import constants
from core.errors import AgentError, InterpretationUnavailableError, NoRoutePossibleError
from core.passwords import hash_token
from routing.models import CategoryFacts
from routing.stops import candidate_of

if TYPE_CHECKING:
    from db.models.thread import Thread

log = logging.getLogger(__name__)

router = APIRouter(tags=["routes"])

NO_PLAN = "no_plan"


def _identity(request: Request) -> str | None:
    """The namespace a run belongs to: the hash of the session that named it.

    Only the hash is ever kept, exactly as the session store keeps it, so the
    journal is addressed by the session and no thread is reachable from another.
    """
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
    thread_id = (
        thread.thread_key(req.session_id, identity=_identity(request)) if req.session_id else None
    )
    if thread_id:
        # The thread the client names is the journal the refinement reconciles
        # against; the snapshot it re-sends is a fallback, not the authority.
        brief = thread.reconcile_brief(repos.threads, thread_id=thread_id, brief=brief)
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

    # What the response says the refinement changed is judged against the route
    # it started from — read before this turn is recorded, so "the previous
    # route" is the last completed turn, not the one about to be written. It is
    # the same key the record below writes under: the journal is namespaced by
    # the session, and reading it under the bare id would find nothing.
    previous: tuple[BriefStop, ...] = ()
    if brief.context is not None:
        previous = thread.previous_stops(repos.threads, thread_id=thread_id, context=brief.context)

    if thread_id:
        thread.record(
            repos.threads,
            thread_id=thread_id,
            query=req.query,
            submission=outcome.submission.model_dump(mode="json"),
            measurement=outcome.measured,
        )

    facts = CategoryFacts.from_rows(repos.taxonomy.all())
    return responses.route_response(req, outcome, repos=repos, facts=facts, previous=previous)


@router.get("/routes/threads/{session_id}", response_model=ThreadResponse)
@deps.storage_guarded
def read_thread(session_id: str, request: Request) -> Any:
    """The turns recorded for this run, read back by the session that named it.

    The journal is account-scoped: without a session there is nothing to read, and
    a run recorded under one session is never returned under another — the key
    carries the session, so the store has no row to hand back.
    """
    user, err = deps.require_user(request, deps.get_user_repository(request))
    if err is not None:
        return err
    assert user is not None
    thread_id = thread.thread_key(session_id, identity=_identity(request))
    journal = deps.get_repos(request).threads.journal(thread_id)
    return _thread_response(session_id, journal)


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

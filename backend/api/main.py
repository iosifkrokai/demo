"""FastAPI agent: turns free-text Russian queries into pedestrian walking routes.

The HTTP layer is deliberately thin: every business decision lives in
agent.planner.pipeline.Pipeline (one orchestrator class). Endpoints:

    GET  /health              liveness + readiness snapshot (db/llm/valhalla)
    POST /routes/generate     body: GenerateReq  -> RouteResponse
    POST /routes/reroute      body: RerouteReq   -> RouteResponse
    POST /routes/explain      body: ExplainReq   -> {explanation: str}

Embeddings and the interpretation agent come from OpenRouter through a single
OPENROUTER_API_KEY.  With no key — or an upstream that times out — the
planner degrades to keyword-only retrieval and the deterministic interpretation
instead of failing; see agent/planner/pipeline.py.  `_call` maps the planner's
own AgentError onto HTTP and never turns a degraded upstream into a bare 500.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import psycopg
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from agent.planner.agent_interpret import DEFAULT_MODEL
from agent.planner.pipeline import Pipeline
from api.routers import accounts as accounts_api, clients as clients_api
from contracts.planner import (
    ExplainReq,
    GenerateReq,
    HealthResponse,
    ParsedQuery,
    RerouteReq,
    RouteResponse,
    RouteSummary,
    ServicesAlongReq,
)
from core.config import openrouter_api_key, settings
from core.errors import AgentError, NoRoutePossible
from infra import embeddings, progress, trace
from store import itineraries as itineraries_mod, places as places_mod, services as services_mod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db = psycopg.connect(settings.DSN, autocommit=True)
    with db.cursor() as cur:
        # trigram similarity threshold for the keyword search fallback
        # (agent/search.py); mirrors db/migrations/0003_trgm_search.sql
        cur.execute("SET pg_trgm.word_similarity_threshold = 0.45")
    app.state.planner = Pipeline(db=db)
    # Warm the local embedding model so the first request does not pay the load.
    embeddings.embed_query("warmup")
    log.info("agent ready (embeddings=%s local, interpret=%s, key=%s)",
             embeddings.MODEL_NAME, DEFAULT_MODEL,
             "set" if openrouter_api_key() else "MISSING")
    if not openrouter_api_key():
        log.warning(
            "no OPENROUTER_API_KEY — deterministic interpretation only: the query "
            + "is read by the regex/keyword parser; retrieval keeps local embeddings "
            + "(routes are still built)"
        )
    yield
    trace.shutdown()
    db.close()


app = FastAPI(title="grodno-poc-agent", lifespan=lifespan)

app.include_router(clients_api.router)
# Accounts, roles, visits and the admin panel (spec 005) — one router, so the
# whole capability is wired in here.
app.include_router(accounts_api.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:80",
        "http://localhost:3000",
        "http://127.0.0.1",
        "http://127.0.0.1:3000",
        "http://host.docker.internal",
    ],
    # Sessions live in a cookie, so a credentialed cross-origin call (a remote
    # VITE_AGENT_URL) must be allowed to carry it. Same-origin — the demo's own
    # nginx — needs no CORS at all; this only widens the door for the listed
    # origins, never to "*" (which the browser rejects together with credentials).
    allow_credentials=True,
    allow_methods=["POST", "GET", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # X-Client-Id carries the anonymous client id the /clients/* routes read and
    # /auth/register adopts; a preflight that does not allow it makes the client
    # entity unreachable from a browser talking to the agent directly (nginx
    # already allows it).
    allow_headers=["Content-Type", "X-Client-Id", "Authorization"],
)


def _call(fn: Callable[[], Any], **kwargs: Any) -> Any:
    """Run a planner call and map its failures onto HTTP.

    AgentError carries the status the planner chose (404 / 422 / 503).  Any
    other failure is a real bug and stays a 500 — the interpretation agent and
    its OpenRouter calls degrade internally (planner/agent_interpret.py returns
    None), so an upstream outage must never reach the client as a 5xx from here.
    """
    try:
        return fn(**kwargs)
    except AgentError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e)) from e


def _no_route_response(req: GenerateReq, detail: str) -> RouteResponse:
    """Turn "there is no walk here" into an answer instead of a failed request.

    The planner raises `NoRoutePossible` when nothing it retrieved can be walked
    together (the sights pool and the services retry both end below two stops).
    That used to leave as HTTP 422 carrying the optimizer's own English sentence:
    the client got no plan, no reason code and nothing to render, and a wording
    the walk cannot serve killed the request outright — the same question asked
    with one extra clause answered 200. The coverage gate already says "no" this
    way (an ordinary response with `status="infeasible"`), and this is the same
    class of no, so it is the same shape: an empty plan, a machine-readable
    reason in `debug`, and a sentence written for the tourist rather than for
    whoever reads the logs.
    """
    return RouteResponse(
        parsed=ParsedQuery(
            time_budget_minutes=req.time_budget_minutes, source="fallback"
        ),
        # No stops and no line: nothing was planned, and inventing look-alikes
        # would be worse than an honest empty answer.
        points=[],
        shape={},
        summary=RouteSummary(length_km=None, time_seconds=None),
        budget=None,
        explanation=(
            "Маршрут не построен: в этой зоне не нашлось остановок, между которыми "
            "можно пройти. Уточните запрос или расширьте район."
        ),
        status="infeasible",
        requirements=[],
        costing=req.profile or "pedestrian",
        changes=None,
        debug={"reason": "no_walkable_route", "detail": detail},
    )


@app.post("/routes/generate", response_model=RouteResponse)
def generate(req: GenerateReq) -> RouteResponse:
    """Build a route. With a `progress_id`, the work is reported as it happens."""
    progress.begin(req.progress_id)
    trace.begin(req.progress_id, session_id=req.session_id)
    try:
        try:
            return app.state.planner.generate(req=req)
        except NoRoutePossible as exc:
            # Caught before the generic AgentError mapping below, which would
            # have re-labelled it as a 422 carrying the optimizer's sentence.
            # ``plan_status``, not ``status``: the second argument to record() is
            # the step's fate (ok/skipped/error) — a verdict passed there is
            # swallowed into the level and lost from the trace.
            trace.record(
                "response",
                input={"query": req.query, "budget_minutes": req.time_budget_minutes},
                plan_status="infeasible",
                reason="no_route_possible",
            )
            return _no_route_response(req, str(exc))
        except AgentError as exc:
            # Everything else keeps its own status (404 / 422 / 503): those are
            # real refusals and upstream outages the client distinguishes.
            trace.record(
                "response",
                "error",  # this one really is a failed step, so it is marked red
                input={"query": req.query, "budget_minutes": req.time_budget_minutes},
                plan_status="error",
                reason=type(exc).__name__,
                http_status=exc.http_status,
            )
            raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    finally:
        # The tracker stops being interesting the moment the answer exists — and
        # on failure too, so a client polling a rejected request is told so
        # instead of watching a stage freeze.
        progress.finish()
        trace.finish()


@app.get("/routes/trace/{trace_id}")
def route_trace(trace_id: str) -> dict:
    """The structured spans of one run, for a client that wants them raw.

    The same spans are exported to the self-hosted Langfuse (see trace.py); this
    endpoint is the machine-readable fallback. An unknown id is a 404 with a
    reason code, not an empty trace — «этого запуска нет» and «запуск ещё
    ничего не сделал» are different things.
    """
    data = trace.get(trace_id)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail={"reason": "unknown_trace_id"},
        )
    return data


@app.get("/routes/progress/{progress_id}")
def route_progress(progress_id: str) -> dict:
    """Where the pipeline got to, in stage codes the client localises.

    An unknown id is a 404 with a reason code, not an empty stage: «не знаю, где
    мы» and «мы на этапе поиска» are different things, and the client falls back
    to what it can observe itself rather than showing an invented caption.
    """
    snapshot = progress.snapshot(progress_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail={"reason": "unknown_progress_id"},
        )
    return snapshot


@app.post("/routes/reroute", response_model=RouteResponse)
def reroute(req: RerouteReq) -> RouteResponse:
    return _call(app.state.planner.reroute, point_ids=req.point_ids, profile=req.profile)


@app.post("/routes/explain")
def explain(req: ExplainReq) -> dict:
    return {"explanation": _call(app.state.planner.explain_route, point_ids=req.point_ids)}


@app.get("/routes/itineraries")
def itineraries() -> dict:
    """Ready-made routes — curated, and resolved against the live dataset.

    No model is involved: the list is authored, and every stop is read from the
    same places table the planner uses. `missing` names any stop key that no
    longer resolves, so a shortened route is visible as such instead of passing
    for a complete one.
    """
    try:
        items, missing = itineraries_mod.resolve_itineraries(app.state.planner.db)
    except itineraries_mod.ItinerariesUnavailable as exc:
        log.error("itineraries unavailable: %s", exc)
        raise HTTPException(
            status_code=503, detail={"reason": "itineraries_unavailable"}
        ) from exc
    return {"items": items, "missing": missing}


@app.get("/places")
def places() -> dict:
    """The full point catalogue — every place in the dataset, for the «все точки»
    tab. A browse, not a search: no model is involved and nothing is capped by a
    query, so the tourist can see all of it at once.
    """
    return places_mod.list_places(app.state.planner.db)


@app.post("/routes/services")
def services_along_route(req: ServicesAlongReq) -> dict:
    """Secondary points beside the line: cafés, toilets, hotels — never stops.

    Measured, not guessed: the distance from the line is PostGIS geometry, and
    the position along the route comes from the same measurement. The walking
    detour to reach a point is a real Valhalla route and is NOT computed here,
    so every item carries `detour_confirmed: false` — nobody may print «+2 мин»
    from this answer.

    A shape that cannot be measured is a 422 with a reason code: an empty list
    must always mean «измерили, рядом ничего нет».
    """
    # Traced under a server-minted id: the answer carries no id for the client to
    # poll, but the span still groups into the same Langfuse session as the
    # generate that drew the line, through req.session_id.
    trace.begin(uuid.uuid4().hex, session_id=req.session_id)
    try:
        try:
            answer = services_mod.services_along(
                app.state.planner.db,
                req.shape,
                categories=req.categories,
                profile=req.profile or services_mod.DEFAULT_PROFILE,
                max_off_line_m=req.max_off_line_m,
                limit=req.limit or services_mod.MAX_SERVICES,
            )
        except ValueError as exc:
            # A failed step, so the span is marked as one; the machine reason
            # rides beside it instead of taking the level's place.
            trace.record(
                "services",
                "error",
                input={"categories": req.categories, "limit": req.limit},
                reason=str(exc),
            )
            raise HTTPException(status_code=422, detail={"reason": str(exc)}) from exc
        trace.record(
            "services",
            input={"categories": req.categories, "limit": req.limit},
            found=len(answer["items"]),
            capped=answer["capped"],
        )
        return answer
    finally:
        trace.finish()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    snap = app.state.planner.health()
    return HealthResponse(**snap)


if __name__ == "__main__":
    uvicorn.run("agent.main:app", host=settings.HOST, port=settings.PORT)

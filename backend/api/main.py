"""FastAPI agent: turns free-text Russian queries into pedestrian walking routes.

The HTTP layer is thin; business decisions live in planner.pipeline.Pipeline.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from agent.client import DEFAULT_MODEL
from api.routers import accounts as accounts_api, clients as clients_api
from core.config import openrouter_api_key, settings
from core.errors import AgentError, NoRoutePossible
from db.store import itineraries as itineraries_mod
from db.store.areas import PostgresAreaRepository
from db.store.places import PostgresPlaceRepository
from db.store.registry import Repositories
from db.store.services import DEFAULT_PROFILE, MAX_SERVICES
from ml import embeddings
from planner.models import (
    ExplainReq,
    GenerateReq,
    HealthResponse,
    ParsedQuery,
    RerouteReq,
    RouteResponse,
    RouteSummary,
    ServicesAlongReq,
)
from planner.pipeline import Pipeline
from telemetry import progress, trace

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    repos = Repositories(
        places=PostgresPlaceRepository(),
        areas=PostgresAreaRepository(),
    )
    app.state.repos = repos
    app.state.planner = Pipeline(repos=repos)
    embeddings.embed_query("warmup")
    log.info("agent ready (embeddings=%s local, interpret=%s, key=%s)",
             embeddings.MODEL_NAME, DEFAULT_MODEL,
             "set" if openrouter_api_key() else "MISSING")
    if not openrouter_api_key():
        log.warning(
            "no OPENROUTER_API_KEY — the planner has no reader for a request: "
            "/routes/generate, /routes/reroute and /routes/explain will refuse "
            "with 503 llm_not_configured. The catalogue endpoints still answer."
        )
    yield
    trace.shutdown()
    repos.close()


app = FastAPI(title="grodno-poc-agent", lifespan=lifespan)

app.include_router(clients_api.router)
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
    allow_credentials=True,
    allow_methods=["POST", "GET", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Client-Id", "Authorization"],
)


def _require_llm() -> None:
    """Refuse a planning request when the interpreter is not configured.

    Reading a free-text query is the model's job; without a key there is no
    reader. Answering anyway — with a keyword parse — would silently give the
    tourist a worse route and no way to tell, so the request is refused instead.
    """
    if not openrouter_api_key():
        raise HTTPException(
            status_code=503,
            detail={"reason": "llm_not_configured"},
        )


def _call(fn: Callable[[], Any], **kwargs: Any) -> Any:
    """Run a planner call and map its failures onto HTTP.

    AgentError carries the planner's status; any other failure stays a 500.
    """
    try:
        return fn(**kwargs)
    except AgentError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e)) from e


def _no_route_response(req: GenerateReq, detail: str) -> RouteResponse:
    """Turn "there is no walk here" into an answer instead of a failed request.

    Returns an empty plan with `status="infeasible"` and a machine-readable reason.
    """
    return RouteResponse(
        parsed=ParsedQuery(
            time_budget_minutes=req.time_budget_minutes, source="agent"
        ),
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
    _require_llm()
    progress.begin(req.progress_id)
    trace.begin(req.progress_id, session_id=req.session_id)
    try:
        try:
            return app.state.planner.generate(req=req)
        except NoRoutePossible as exc:
            trace.record(
                "response",
                input={"query": req.query, "budget_minutes": req.time_budget_minutes},
                plan_status="infeasible",
                reason="no_route_possible",
            )
            return _no_route_response(req, str(exc))
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


@app.get("/routes/trace/{trace_id}")
def route_trace(trace_id: str) -> dict:
    """The structured spans of one run, for a client that wants them raw.

    An unknown id is a 404 with a reason code, not an empty trace.
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

    An unknown id is a 404 with a reason code, not an empty stage.
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
    _require_llm()
    return _call(app.state.planner.reroute, point_ids=req.point_ids, profile=req.profile)


@app.post("/routes/explain")
def explain(req: ExplainReq) -> dict:
    _require_llm()
    return {"explanation": _call(app.state.planner.explain_route, point_ids=req.point_ids)}


@app.get("/routes/itineraries")
def itineraries() -> dict:
    """Ready-made routes — curated, and resolved against the live dataset.

    `missing` names any stop key that no longer resolves.
    """
    try:
        items, missing = itineraries_mod.resolve_itineraries(app.state.repos.places)
    except itineraries_mod.ItinerariesUnavailable as exc:
        log.error("itineraries unavailable: %s", exc)
        raise HTTPException(
            status_code=503, detail={"reason": "itineraries_unavailable"}
        ) from exc
    return {"items": items, "missing": missing}


@app.get("/places")
def places() -> dict:
    """The full point catalogue — every place in the dataset, for the «все точки» tab.

    A browse, not a search: no model is involved and nothing is capped by a query.
    """
    return app.state.repos.places.catalog()


@app.post("/routes/services")
def services_along_route(req: ServicesAlongReq) -> dict:
    """Secondary points beside the line: cafés, toilets, hotels — never stops.

    Distance and position are measured; items carry `detour_confirmed: false`.
    """
    trace.begin(uuid.uuid4().hex, session_id=req.session_id)
    try:
        try:
            answer = app.state.repos.places.services_along(
                req.shape,
                categories=req.categories,
                profile=req.profile or DEFAULT_PROFILE,
                max_off_line_m=req.max_off_line_m,
                limit=req.limit or MAX_SERVICES,
            )
        except ValueError as exc:
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
    uvicorn.run("api.main:app", host=settings.HOST, port=settings.PORT)

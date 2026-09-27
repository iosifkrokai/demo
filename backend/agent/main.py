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
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import psycopg
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import (
    clients_api,
    constants,
    itineraries as itineraries_mod,
    services as services_mod,
)
from .config import openrouter_api_key, settings
from .errors import AgentError
from .models import (
    ExplainReq,
    GenerateReq,
    HealthResponse,
    RerouteReq,
    RouteResponse,
    ServicesAlongReq,
)
from .planner.agent_interpret import DEFAULT_MODEL
from .planner.pipeline import Pipeline

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
    log.info("agent ready (OpenRouter: embed=%s, interpret=%s, key=%s)",
             constants.EMBED_MODEL, DEFAULT_MODEL,
             "set" if openrouter_api_key() else "MISSING")
    if not openrouter_api_key():
        log.warning(
            "no OPENROUTER_API_KEY — degraded keyword-only mode: no embeddings, "
            + "deterministic interpretation (routes are still built)"
        )
    yield
    db.close()


app = FastAPI(title="grodno-poc-agent", lifespan=lifespan)

app.include_router(clients_api.router)

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
    allow_credentials=False,
    allow_methods=["POST", "GET", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
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


@app.post("/routes/generate", response_model=RouteResponse)
def generate(req: GenerateReq) -> RouteResponse:
    return _call(app.state.planner.generate, req=req)


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
    try:
        return services_mod.services_along(
            app.state.planner.db,
            req.shape,
            categories=req.categories,
            profile=req.profile or services_mod.DEFAULT_PROFILE,
            max_off_line_m=req.max_off_line_m,
            limit=req.limit or services_mod.MAX_SERVICES,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"reason": str(exc)}) from exc


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    snap = app.state.planner.health()
    return HealthResponse(**snap)


if __name__ == "__main__":
    uvicorn.run("agent.main:app", host=settings.HOST, port=settings.PORT)

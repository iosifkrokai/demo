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

from . import clients_api, constants
from .config import openrouter_api_key, settings
from .errors import AgentError
from .models import (
    ExplainReq,
    GenerateReq,
    HealthResponse,
    RerouteReq,
    RouteResponse,
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


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    snap = app.state.planner.health()
    return HealthResponse(**snap)


if __name__ == "__main__":
    uvicorn.run("agent.main:app", host=settings.HOST, port=settings.PORT)

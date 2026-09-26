"""FastAPI agent: turns free-text Russian queries into pedestrian walking routes.

The HTTP layer is deliberately thin: every business decision lives in
agent.planner.pipeline.Pipeline (one orchestrator class). Endpoints:

    GET  /health              liveness + readiness snapshot (db/llm/valhalla)
    POST /routes/generate     body: GenerateReq  -> RouteResponse
    POST /routes/reroute      body: RerouteReq   -> RouteResponse
    POST /routes/explain      body: ExplainReq   -> {explanation: str}

Embeddings, intent and rerank come from OpenRouter through a single
OPENROUTER_API_KEY.  With no key — or an upstream that times out — the
planner degrades to keyword-only retrieval instead of failing; see
agent/planner/pipeline.py.  `_call` is the last-resort net: an upstream
error that somehow escaped the degradation paths is reported as a 503
with a readable detail, never as a bare 500.
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

from . import constants, jev
from .config import settings
from .errors import AgentError
from .jev import JevError
from .models import (
    ExplainReq,
    GenerateReq,
    HealthResponse,
    RerouteReq,
    RouteResponse,
)
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
    log.info("agent ready (OpenRouter: embed=%s, jev=%s, key=%s)",
             constants.EMBED_MODEL, constants.JEV_MODEL,
             "set" if jev.available() else "MISSING")
    if not jev.available():
        log.warning(
            "no OPENROUTER_API_KEY — degraded keyword-only mode: no embeddings, "
            + "deterministic intent, no Jev rerank (routes are still built)"
        )
    yield
    db.close()


app = FastAPI(title="grodno-poc-agent", lifespan=lifespan)

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
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


def _call(fn: Callable[[], Any], **kwargs: Any) -> Any:
    """Run a planner call and map its failures onto HTTP.

    AgentError carries the status the planner chose (404 / 422 / 503).  A
    JevError that got here means the degraded paths did not cover it: report
    it as 503 with the reason, so an OpenRouter outage can never reach the
    client as an opaque 500.  Anything else is a real bug and stays a 500.
    """
    try:
        return fn(**kwargs)
    except AgentError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e)) from e
    except JevError as e:
        log.warning("openrouter error escaped the planner: %s", e)
        raise HTTPException(
            status_code=503, detail=f"OpenRouter unavailable: {e}",
        ) from e


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

"""FastAPI agent: turns free-text Russian queries into pedestrian walking routes.

The HTTP layer is deliberately thin: every business decision lives in
agent.planner.pipeline.Pipeline (one orchestrator class). Endpoints:

    GET  /health              liveness + readiness snapshot (db/llm/valhalla)
    POST /routes/generate     body: GenerateReq  -> RouteResponse
    POST /routes/reroute      body: RerouteReq   -> RouteResponse
    POST /routes/explain      body: ExplainReq   -> {explanation: str}

All models use OpenRouter (embeddings + intent + rerank). No local ML models.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import psycopg
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import constants
from .config import settings
from .errors import AgentError
from .models import (
    ExplainReq,
    GenerateReq,
    HealthResponse,
    RouteResponse,
    RerouteReq,
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
             "set" if settings.OPENROUTER_API_KEY else "MISSING")
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


@app.post("/routes/generate", response_model=RouteResponse)
def generate(req: GenerateReq) -> RouteResponse:
    try:
        return app.state.planner.generate(req)
    except AgentError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))


@app.post("/routes/reroute", response_model=RouteResponse)
def reroute(req: RerouteReq) -> RouteResponse:
    try:
        return app.state.planner.reroute(req.point_ids, req.profile)
    except AgentError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))


@app.post("/routes/explain")
def explain(req: ExplainReq) -> dict:
    try:
        return {"explanation": app.state.planner.explain_route(req.point_ids)}
    except AgentError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    snap = app.state.planner.health()
    return HealthResponse(**snap)


if __name__ == "__main__":
    uvicorn.run("agent.main:app", host=settings.HOST, port=settings.PORT)

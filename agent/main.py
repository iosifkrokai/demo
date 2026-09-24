"""FastAPI agent: turns free-text Russian queries into pedestrian walking routes.

The HTTP layer is deliberately thin: every business decision lives in
agent.planner.pipeline.Pipeline (one orchestrator class). Endpoints:

    GET  /health              liveness + readiness snapshot (db/llm/valhalla/embedder)
    POST /routes/generate     body: GenerateReq  -> RouteResponse
    POST /routes/reroute      body: RerouteReq   -> RouteResponse
    POST /routes/explain      body: ExplainReq   -> {explanation: str}

The embedder is loaded once at startup (lifespan). The intent extractor
hits DeepInfra Gemini Flash per request (latency ~600-800ms, ~$0.0001).
Everything else (retrieval, rerank, MMR, route optimization, Valhalla) is
local.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import psycopg
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastembed import TextEmbedding

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
    log.info("loading embedder: %s (one-time)...", settings.EMBED_MODEL)
    embedder = TextEmbedding(settings.EMBED_MODEL)
    db = psycopg.connect(settings.DSN, autocommit=True)
    app.state.planner = Pipeline(embedder=embedder, db=db)
    # Pre-warm the cross-encoder in the background so the first request
    # doesn't pay the 1-2s model load cost. Failures are non-fatal.
    try:
        from .planner.rerank import warmup as warmup_rerank
        if warmup_rerank():
            log.info("cross-encoder ready: %s", settings.BGE_RERANK_MODEL)
        else:
            log.info("cross-encoder disabled (RERANK_BACKEND=%s)", settings.RERANK_BACKEND)
    except Exception as e:
        log.warning("cross-encoder warmup failed: %s", e)
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
        return app.state.planner.reroute(req.point_ids)
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

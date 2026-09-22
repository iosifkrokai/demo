"""FastAPI agent: turns free-text Russian queries into pedestrian walking routes.

The HTTP layer is deliberately thin: every business decision lives in
RoutePlanner (agent.py), which is easy to unit-test without FastAPI. Endpoints:

    GET  /health              liveness + readiness snapshot (db/llm/valhalla/embedder)
    POST /routes/generate     body: GenerateReq  -> RouteResponse
    POST /routes/reroute      body: RerouteReq   -> RouteResponse
    POST /routes/explain      body: ExplainReq   -> {explanation: str}

The embedder is loaded once at startup (lifespan). Same model is used by the
DB-load step (enrich_places.py). The e5 family requires a "query: " prefix on
queries; passages get "passage: " in enrich.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import psycopg
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastembed import TextEmbedding

from .agent import RoutePlanner
from .config import settings
from .errors import AgentError
from .llm import init as init_llm
from .models import (
    ExplainReq,
    GenerateReq,
    HealthResponse,
    RouteResponse,
    RerouteReq,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("loading %s via fastembed (one-time)...", settings.EMBED_MODEL)
    embedder = TextEmbedding(settings.EMBED_MODEL)
    init_llm()  # local GGUF via llama-cpp-python; blocking first-time download
    db = psycopg.connect(settings.DSN, autocommit=True)
    app.state.planner = RoutePlanner(embedder=embedder, db=db)
    yield
    db.close()


app = FastAPI(title="grodno-poc-agent", lifespan=lifespan)

# The webapp (port 80) and the agent (port 8080) are different origins, so the
# browser fires a CORS preflight before fetch(). Locking origins to localhost
# is fine for the POC.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://localhost:80", "http://127.0.0.1"],
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
        return {"explanation": app.state.planner.explain(req.point_ids)}
    except AgentError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    snap = app.state.planner.health()
    return HealthResponse(**snap)


if __name__ == "__main__":
    uvicorn.run("agent.main:app", host=settings.HOST, port=settings.PORT)

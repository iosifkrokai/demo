"""FastAPI agent: turns free-text Russian queries into pedestrian walking routes.

The HTTP layer is thin; business decisions live in planner.pipeline.Pipeline.
The app object lives here because ``uvicorn api.main:app`` and the image's
``python -m api.main`` both name it; the routes themselves live under
``api.routers``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.client import DEFAULT_MODEL
from api.routers import (
    accounts as accounts_api,
    admin as admin_api,
    catalogue as catalogue_api,
    clients as clients_api,
    health as health_api,
    routes as routes_api,
)
from core.config import openrouter_api_key, settings
from db.store.areas import PostgresAreaRepository
from db.store.clients import PostgresClientRepository
from db.store.places import PostgresPlaceRepository
from db.store.registry import Repositories
from db.store.stats import PostgresStatsRepository
from db.store.users import PostgresUserRepository
from ml import embeddings
from planner.pipeline import Pipeline
from telemetry import trace

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Re-exported: tests reach the shared planner caller and the two telemetry reads
# through `api.main`, the module they have always come from.
@asynccontextmanager
async def lifespan(_: FastAPI):
    repos = Repositories(
        places=PostgresPlaceRepository(),
        areas=PostgresAreaRepository(),
        users=PostgresUserRepository(),
        stats=PostgresStatsRepository(),
        clients=PostgresClientRepository(),
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
app.include_router(admin_api.router)
app.include_router(routes_api.router)
app.include_router(catalogue_api.router)
app.include_router(health_api.router)

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


if __name__ == "__main__":
    uvicorn.run("api.main:app", host=settings.HOST, port=settings.PORT)

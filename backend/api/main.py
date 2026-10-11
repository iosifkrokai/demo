"""FastAPI agent: turns free-text Russian queries into pedestrian walking routes.

The HTTP layer is thin; the decisions live in the agent's tools (`agent/tools`).
The app object lives here because ``uvicorn api.main:app`` and the image's
``python -m api.main`` both name it; the routes themselves live under
``api.routers``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import logfire
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent import llm
from api.routers import (
    accounts as accounts_api,
    admin as admin_api,
    catalogue as catalogue_api,
    clients as clients_api,
    health as health_api,
    routes as routes_api,
)
from core.config import LOGFIRE_TOKEN, settings
from db.embeddings import model
from db.store.areas import PostgresAreaRepository
from db.store.clients import PostgresClientRepository
from db.store.places import PostgresPlaceRepository
from db.store.registry import Repositories
from db.store.stats import PostgresStatsRepository
from db.store.users import PostgresUserRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Observability is wired once here. PydanticAI instruments itself through
    # Logfire, so every model call and every tool call of the agent loop is
    # visible without a hand-written span. Containers and CI carry no token, and
    # send_to_logfire="if-token-present" makes that absence a no-op, not an error.
    logfire.configure(send_to_logfire="if-token-present", token=LOGFIRE_TOKEN)
    logfire.instrument_pydantic_ai()
    repos = Repositories(
        places=PostgresPlaceRepository(),
        areas=PostgresAreaRepository(),
        users=PostgresUserRepository(),
        stats=PostgresStatsRepository(),
        clients=PostgresClientRepository(),
    )
    app.state.repos = repos
    model.embed_text("warmup")
    log.info(
        "agent ready (embeddings=%s local, model=%s, key=%s)",
        model.MODEL_NAME,
        llm.model_name(),
        "set" if llm.api_key() else "MISSING",
    )
    if not llm.api_key():
        log.warning(
            f"no {llm.provider()} key — the planner has no reader for a request: "
            "/routes/generate, /routes/reroute and /routes/explain will refuse "
            "with 503 llm_not_configured. The catalogue endpoints still answer."
        )
    yield
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

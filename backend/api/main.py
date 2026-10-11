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
    health as health_api,
    routes as routes_api,
)
from core.config import logfire_token, settings
from db.embeddings import model
from db.store.areas import PostgresAreaRepository
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
    logfire.configure(send_to_logfire="if-token-present", token=logfire_token())
    logfire.instrument_pydantic_ai()
    repos = Repositories(
        places=PostgresPlaceRepository(),
        areas=PostgresAreaRepository(),
        users=PostgresUserRepository(),
        stats=PostgresStatsRepository(),
    )
    app.state.repos = repos
    model.embed_text("warmup")
    chosen = llm.chosen_provider()
    log.info(
        "agent ready (embeddings=%s local, provider=%s, model=%s, key=%s)",
        model.MODEL_NAME,
        chosen.name,
        llm.model_name(),
        "set" if chosen.api_key else "MISSING",
    )
    if not chosen.api_key:
        log.warning(
            f"no {chosen.name} key — /routes/generate, /routes/reroute and "
            "/routes/explain will refuse with 503 llm_not_configured. "
            "The catalogue endpoints still answer."
        )
    yield
    repos.close()


app = FastAPI(title="grodno-poc-agent", lifespan=lifespan)

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
    allow_headers=["Content-Type", "Authorization"],
)


if __name__ == "__main__":
    uvicorn.run("api.main:app", host=settings.HOST, port=settings.PORT)

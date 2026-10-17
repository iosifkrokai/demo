"""HTTP surface for the health probe."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from agent import llm
from api import deps
from api.models.routes import HealthResponse
from db.embeddings import model
from routing.valhalla.http import ping as valhalla_ping

router = APIRouter(tags=["health"])


def _valhalla_ok() -> bool:
    return bool(valhalla_ping())


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    db_ok = deps.get_repos(request).places.ping()
    valhalla_ok = _valhalla_ok()
    embedder_ok = model.is_available()
    llm_ok = bool(llm.chosen_provider().api_key)
    payload: dict[str, Any] = {
        "status": "ok" if (db_ok and valhalla_ok and embedder_ok and llm_ok) else "degraded",
        "embedder": embedder_ok,
        "llm": llm_ok,
        "db": db_ok,
        "valhalla": valhalla_ok,
    }
    return HealthResponse(**payload)

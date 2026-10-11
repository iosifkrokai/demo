"""HTTP surface for the health probe.

Readiness is the honest sum of what a planning request needs: a database, a
routing engine, a local embedder, and a configured model. Any of them missing is
reported as such rather than as a plain "ok".
"""

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
    try:
        return bool(valhalla_ping())
    except Exception:  # unreachable is exactly what this answers
        return False


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    db_ok = deps.get_repos(request).places.ping()
    valhalla_ok = _valhalla_ok()
    embedder_ok = model.is_available()
    llm_ok = bool(llm.chosen_provider().api_key)
    payload: dict[str, Any] = {
        # A missing reader is not "ok": nothing can be planned without one.
        "status": "ok" if (db_ok and valhalla_ok and embedder_ok and llm_ok) else "degraded",
        "embedder": embedder_ok,
        "llm": llm_ok,
        "db": db_ok,
        "valhalla": valhalla_ok,
    }
    return HealthResponse(**payload)

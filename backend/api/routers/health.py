"""HTTP surface for the health probe."""

from __future__ import annotations

from fastapi import APIRouter, Request

from api import deps
from planner.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    return HealthResponse(**deps.get_planner(request).health())

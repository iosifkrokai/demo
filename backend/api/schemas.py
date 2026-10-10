"""HTTP-only request/response DTOs.

Wire-only shapes; planner currencies live in contracts/planner.py.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RerouteReq(BaseModel):
    point_ids: list[int] = Field(min_length=2, max_length=10)
    profile: Literal[
        "pedestrian", "bicycle", "auto", "car", "truck", "bus",
        "motor_scooter", "motorcycle",
    ] | None = None


class ExplainReq(BaseModel):
    point_ids: list[int] = Field(min_length=2, max_length=10)


class ServicesAlongReq(BaseModel):
    """POST /routes/services body: the line the tourist is walking.

    `shape` is the GeoJSON LineString the client already has.
    """

    shape: dict = Field(description="GeoJSON LineString, WGS84")
    profile: Literal[
        "pedestrian", "bicycle", "auto", "car", "truck", "bus",
        "motor_scooter", "motorcycle", "any",
    ] | None = None
    categories: list[str] | None = Field(default=None, max_length=8)
    max_off_line_m: float | None = Field(default=None, ge=10, le=1500)
    limit: int | None = Field(default=None, ge=1, le=20)
    session_id: str | None = Field(default=None, max_length=128)


class HealthResponse(BaseModel):
    status: Literal["ok", "starting", "degraded"]
    embedder: bool
    llm: bool
    db: bool
    valhalla: bool

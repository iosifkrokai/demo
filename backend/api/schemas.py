"""HTTP-only request/response DTOs.

These are the wire shapes of the API surface — the bodies and responses that
exist only to cross the HTTP boundary. None of them travel into the pipeline
or the data layer; the planner currencies shared with those live in
contracts/planner.py.
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

    `shape` is the GeoJSON LineString the client already has (the same one
    Valhalla returns for the route it drew), so nothing has to be recomputed to
    ask «что есть по пути».
    """

    shape: dict = Field(description="GeoJSON LineString, WGS84")
    # Valhalla's own costing names, as everywhere else in this API.
    profile: Literal[
        "pedestrian", "bicycle", "auto", "car", "truck", "bus",
        "motor_scooter", "motorcycle", "any",
    ] | None = None
    # Category codes from data/taxonomy.csv; only the service ones are honoured.
    categories: list[str] | None = Field(default=None, max_length=8)
    # An explicit gate in metres; None means «по профилю» (150 m walking).
    max_off_line_m: float | None = Field(default=None, ge=10, le=1500)
    limit: int | None = Field(default=None, ge=1, le=20)
    # The guide run this «что по пути» belongs to — same id the generate that
    # drew the line carried, so its trace lands in the same Langfuse session.
    session_id: str | None = Field(default=None, max_length=128)


class HealthResponse(BaseModel):
    status: Literal["ok", "starting", "degraded"]
    # embedder is the LOCAL CPU model (infra.embeddings): available whenever the
    # image is built, with or without OPENROUTER_API_KEY.
    embedder: bool
    # llm is the interpretation agent over OpenRouter — optional.
    llm: bool
    # Which reader answers the query: the LLM agent or the deterministic parser.
    interpretation: Literal["llm", "deterministic"]
    db: bool
    valhalla: bool

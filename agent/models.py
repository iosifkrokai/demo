"""Pydantic models for requests and responses."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .config import settings

CategoryLiteral = Literal[
    "замок", "дворец", "усадьба", "костёл", "церковь", "монастырь",
    "храм", "музей", "архитектура", "парк", "памятник",
    "инфраструктура", "кладбище",
]


# ---------- Requests ----------

class GenerateReq(BaseModel):
    """POST /routes/generate body.

    All optional knobs have agent-side defaults (see config.settings). If a
    field is omitted, the agent falls back to the value the local LLM parsed
    out of the query text, or — if that fails — to config defaults.

    Sending a value explicitly (even if it matches the default) makes the
    agent use YOUR value; the LLM cannot override an explicit client request.
    """
    query: str = Field(min_length=3, max_length=500)

    # Explicit client overrides. None means "decide for me".
    n_points: int | None = Field(default=None, ge=settings.MIN_N_POINTS, le=settings.MAX_N_POINTS)
    time_budget_minutes: int | None = Field(default=None, ge=settings.MIN_BUDGET_MIN, le=settings.MAX_BUDGET_MIN)
    region_bbox: list[float] | None = Field(
        default=None,
        description="[south, west, north, east]. Used as a PostGIS envelope filter.",
        min_length=4, max_length=4,
    )

    # Behaviour flags
    allow_auto_relax: bool = Field(
        default=True,
        description="If the time budget is too tight, drop a stop instead of failing.",
    )

    @field_validator("query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be empty")
        return v

    @field_validator("region_bbox")
    @classmethod
    def _check_bbox(cls, v):
        if v is None:
            return v
        if len(v) != 4:
            raise ValueError("region_bbox must be [south, west, north, east]")
        s, w, n, e = v
        b = settings.GRODNO_BBOX
        # Pad the bounds so user-supplied boxes near the edge of Grodno still work.
        if not (b["south"] - 0.5 <= s <= n <= b["north"] + 0.2 and
                b["west"]  - 0.5 <= w <= e <= b["east"]  + 0.5):
            raise ValueError(f"region_bbox {v} outside Grodno region bounds")
        return v


class RerouteReq(BaseModel):
    """POST /routes/reroute body — re-route a chosen list of place IDs."""
    point_ids: list[int] = Field(min_length=2, max_length=10)


class ExplainReq(BaseModel):
    """POST /routes/explain — explain a pre-built route in natural Russian."""
    point_ids: list[int] = Field(min_length=2, max_length=10)


# ---------- Responses ----------

class Place(BaseModel):
    id: int
    name: str
    category: str | None
    lat: float
    lon: float
    blurb: str | None = None
    visit_minutes: int | None = None


class ParsedQuery(BaseModel):
    keywords: list[str] = []
    categories: list[str] = []
    n_points: int | None = None
    time_budget_minutes: int | None = None
    region_bbox: list[float] | None = None
    source: Literal["llm", "fallback", "explicit"] = "llm"


class BudgetInfo(BaseModel):
    budget_minutes: int
    walk_minutes: int
    visit_minutes: int
    total_minutes: int
    fits: bool
    stops_dropped: int = 0


class RouteSummary(BaseModel):
    length_km: float | None = None
    time_seconds: float | None = None


class RouteResponse(BaseModel):
    parsed: ParsedQuery
    points: list[Place]
    shape: dict  # GeoJSON LineString
    summary: RouteSummary
    budget: BudgetInfo | None = None
    explanation: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "starting", "degraded"]
    embedder: bool
    llm: bool
    db: bool
    valhalla: bool


# ---------- Internal planner objects ----------

class Candidate(BaseModel):
    """One candidate place with the bits the planner needs."""
    id: int
    name: str
    category: str | None
    lat: float
    lon: float
    blurb: str | None = None
    relevance: float = 0.0  # higher is better (1 - cosine_distance)


class Plan(BaseModel):
    """Result of the planner before we hit Valhalla for the final route."""
    candidates: list[Candidate]
    ordered_ids: list[int]
    reasoning: dict = Field(default_factory=dict)

"""Pydantic models for requests and responses.

Two parallel surfaces live here:
  * HTTP-facing models (GenerateReq, RouteResponse, ...) used by main.py.
  * Internal planner models (IntentDecision, ResolvedConstraints, CostMatrix,
    ValidatedPlan) used by agent.planner.* — not exposed over HTTP.

The legacy Candidate/Place pydantic models stay as-is for backwards compat
with agent/agent.py (the old RoutePlanner). The new pipeline uses these too.
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, field_validator

from .config import settings


# ============================================================================
# HTTP — Requests
# ============================================================================

CategoryLiteral = Literal[
    "замок", "дворец", "усадьба", "костёл", "церковь", "монастырь",
    "храм", "музей", "архитектура", "парк", "памятник",
    "инфраструктура", "кладбище",
]

EraLiteral = Literal["any", "pre1900", "soviet", "modern"]
IntentTypeLiteral = Literal["discovery", "specific", "themed", "vague"]
PartyTypeLiteral = Literal["solo", "family", "couple", "group"]


class GenerateReq(BaseModel):
    """POST /routes/generate body."""
    query: str = Field(min_length=3, max_length=500)
    time_budget_minutes: int | None = Field(default=None, ge=settings.MIN_BUDGET_MIN, le=settings.MAX_BUDGET_MIN)
    region_bbox: list[float] | None = Field(
        default=None,
        description="[south, west, north, east]. Used as a PostGIS envelope filter.",
        min_length=4, max_length=4,
    )
    allow_auto_relax: bool = Field(default=True)
    conversation_id: str | None = Field(default=None)
    user_interests: list[str] | None = Field(default=None)
    preferences: dict | None = Field(default=None)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be empty")
        return v


class RerouteReq(BaseModel):
    point_ids: list[int] = Field(min_length=2, max_length=10)


class ExplainReq(BaseModel):
    point_ids: list[int] = Field(min_length=2, max_length=10)


# ============================================================================
# HTTP — Responses
# ============================================================================

class Place(BaseModel):
    id: int
    name: str
    category: str | None
    lat: float
    lon: float
    blurb: str | None = None
    fun_fact: str | None = None
    fun_facts: list[str] = []
    links: list[dict] = []
    visit_minutes: int | None = None


class ParsedQuery(BaseModel):
    keywords: list[str] = []
    categories: list[str] = []
    time_budget_minutes: int | None = None
    source: Literal["llm", "fallback", "explicit", "gemini", "regex"] = "llm"


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
    shape: dict
    summary: RouteSummary
    budget: BudgetInfo | None = None
    explanation: str | None = None
    debug: dict | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "starting", "degraded"]
    embedder: bool
    llm: bool
    db: bool
    valhalla: bool


# ============================================================================
# Internal planner — Step 0 (preprocess)
# ============================================================================

class PreprocessedQuery(BaseModel):
    raw: str
    normalized: str
    language: str = "ru"
    is_short: bool = False
    is_specific: bool = False
    fingerprint: str = ""
    n_significant_words: int = 0


# ============================================================================
# Internal planner — Step 1 (intent extraction)
# ============================================================================

class IntentDecision(BaseModel):
    intent_type: IntentTypeLiteral = "discovery"
    categories_pos: list[CategoryLiteral] = []
    categories_neg: list[CategoryLiteral] = []
    keywords_pos: list[str] = []
    keywords_neg: list[str] = []
    named_places: list[str] = []
    narrative: list[str] = []
    time_budget_minutes: int | None = None
    era_hint: EraLiteral = "any"
    party_type: PartyTypeLiteral = "solo"


class IntentResult(BaseModel):
    decision: IntentDecision
    source: Literal["gemini", "regex"] = "regex"
    confidence: float = 1.0
    latency_ms: int = 0
    raw_response: dict | None = None


# ============================================================================
# Internal planner — Step 2 (constraint resolution)
# ============================================================================

class ResolvedConstraints(BaseModel):
    must_visit_ids: list[int] = []
    optional_categories: list[str] = []
    forbidden_categories: list[str] = []
    forbidden_keywords: list[str] = []
    time_budget_minutes: int = 120
    bbox: tuple[float, float, float, float] | None = None  # (W, S, E, N)
    era_hint: EraLiteral = "any"
    party_type: str = "solo"
    intent_type: str = "discovery"
    must_visit_keywords: list[str] = []
    query_keywords: list[str] = []


# ============================================================================
# Internal planner — Steps 3-4 (retrieval / reranking / MMR)
# ============================================================================

class Candidate(BaseModel):
    """One candidate place with the bits the planner needs."""
    id: int
    name: str
    category: str | None
    lat: float
    lon: float
    blurb: str | None = None
    fun_fact: str | None = None
    fun_facts: list[str] = []
    links: list[dict] = []
    relevance: float = 0.0          # higher is better
    rrf_score: float = 0.0          # RRF signal strength
    rerank_score: float | None = None  # cross-encoder score


# ============================================================================
# Internal planner — Step 5 (cost matrix)
# ============================================================================

class CostMatrix(BaseModel):
    """Walk cost matrix + per-place visit times."""
    walk_seconds: list[list[float]] = []
    visit_minutes: list[int] = []
    indices: list[int] = []  # indexes into the source candidate list


# ============================================================================
# Internal planner — Step 7 (validated plan)
# ============================================================================

class ValidatedPlan(BaseModel):
    route: list[Candidate]
    walk_seconds: float
    visit_seconds: int
    total_seconds: int
    fits_budget: bool
    stops_dropped: int = 0
    trace: dict = Field(default_factory=dict)


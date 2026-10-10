"""Pydantic models for requests and responses.

HTTP-facing models plus internal planner models used by planner.*.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from core import constants
from domain.taxonomy import all_codes

CategoryLiteral = Literal[*all_codes()]

EraLiteral = Literal["any", "pre1900", "soviet", "modern"]
IntentTypeLiteral = Literal["discovery", "specific", "themed", "vague"]
PartyTypeLiteral = Literal["solo", "family", "couple", "group"]
SearchScopeLiteral = Literal["town", "district", "region"]


class LatLon(BaseModel):
    """Tourist's current position (device geolocation)."""
    lat: float = Field(ge=44.0, le=62.0)
    lon: float = Field(ge=19.0, le=42.0)


class ContextPoint(BaseModel):
    """A stop the already-built route has, sent back on a refinement turn."""

    id: int | None = None
    name: str = Field(max_length=200)
    lat: float
    lon: float
    pinned: bool = False
    source: Literal["agent", "user", "mine"] = "agent"


class RouteContext(BaseModel):
    """The route as it stands plus what the user asks to change about it.

    `instruction` is the delta only; `base_points` are forced to survive it.
    """

    instruction: str | None = Field(default=None, max_length=500)
    base_points: list[ContextPoint] = Field(default_factory=list)
    excluded_ids: list[int] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)


class RouteChange(BaseModel):
    id: int | None = None
    name: str
    reason: str | None = None


class RouteChanges(BaseModel):
    """What a refinement did to the previous route — shown to the user honestly."""

    added: list[RouteChange] = Field(default_factory=list)
    removed: list[RouteChange] = Field(default_factory=list)
    kept: int = 0


class GenerateReq(BaseModel):
    """POST /routes/generate body."""
    query: str = Field(min_length=3, max_length=500)
    progress_id: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=128)
    time_budget_minutes: int | None = Field(
        default=None, ge=0, le=constants.MAX_BUDGET_MIN
    )
    origin: LatLon | None = None
    profile: Literal[
        "pedestrian", "bicycle", "auto", "car", "truck", "bus",
        "motor_scooter", "motorcycle",
    ] | None = None
    region_bbox: list[float] | None = Field(
        default=None,
        description="[south, west, north, east]. Used as a PostGIS envelope filter.",
        min_length=4, max_length=4,
    )
    context: RouteContext | None = None

    locale: Literal["ru", "en"] = "ru"
    party_adults: int | None = Field(default=None, ge=0, le=50)
    party_children: int | None = Field(default=None, ge=0, le=20)
    party_children_ages: list[int] = Field(default_factory=list)
    mobility: list[str] = Field(default_factory=list)
    hard_services: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    result_mode: Literal["route", "catalogue"] = "route"
    round_trip: bool = False

    @field_validator("query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be empty")
        return v


class Photo(BaseModel):
    """A picture of a point, always with the credit the licence demands.

    url, author, license and source travel together; incomplete records are never emitted.
    """

    url: str
    author: str
    license: str
    source: str | None = None


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
    photo: Photo | None = None
    visit_minutes: int | None = None
    opening_hours: str | None = None
    ticket_price: str | None = None
    town: str | None = None
    district: str | None = None


class ParsedQuery(BaseModel):
    keywords: list[str] = []
    categories: list[str] = []
    time_budget_minutes: int | None = None
    source: Literal["agent", "explicit"] = "agent"


class BudgetInfo(BaseModel):
    budget_minutes: int | None = None
    walk_minutes: int
    visit_minutes: int
    total_minutes: int
    fits: bool
    stops_dropped: int = 0


class RouteSummary(BaseModel):
    length_km: float | None = None
    time_seconds: float | None = None


class RequirementSignal(BaseModel):
    """One requirement as the client renders it — codes only, never prose.

    `origin` is the honest provenance; nothing here is a sentence in any language.
    """

    kind: Literal["must_visit", "service", "interest", "avoid"]
    strength: Literal["hard", "soft"] = "soft"
    code: str | None = None
    name: str | None = None
    origin: Literal["ui", "agent"] = "agent"
    status: Literal["satisfied", "unmet", "uncertain", "pending"] = "pending"
    reason: str | None = None
    place_ids: list[int] = Field(default_factory=list)


OverallStatus = Literal["ready", "infeasible", "degraded", "pending"]


class Interpretation(BaseModel):
    """What the system understood — one place, before the plan is judged.

    Every field is a code or a number; the wording is the client's.
    """

    source: Literal["llm", "mixed", "explicit"] = "llm"
    locale: Literal["ru", "en"] = "ru"
    status: OverallStatus = "pending"

    adults: int | None = None
    children: int | None = None
    children_ages: list[int] = Field(default_factory=list)
    mobility: list[str] = Field(default_factory=list)
    budget_minutes: int | None = None
    areas: list[str] = Field(default_factory=list)
    transport: str | None = None
    result_mode: Literal["route", "catalogue"] = "route"
    round_trip: bool = False

    requirements: list[RequirementSignal] = Field(default_factory=list)
    unmet: list[RequirementSignal] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class PlannedAlternative(BaseModel):
    """A way to actually make the plan, offered when the chosen one does not fit.

    An offer with a reason code, not a silent switch of the request.
    """

    costing: str
    reason: str
    note: str


class RouteResponse(BaseModel):
    parsed: ParsedQuery
    points: list[Place]
    shape: dict
    summary: RouteSummary
    result_mode: Literal["route", "catalogue"] = "route"
    budget: BudgetInfo | None = None
    explanation: str | None = None
    status: str | None = None
    requirements: list[dict] | None = None
    interpretation: Interpretation | None = None
    costing: str | None = None
    changes: RouteChanges | None = None
    alternatives: list[PlannedAlternative] | None = None
    debug: dict | None = None


class PreprocessedQuery(BaseModel):
    raw: str
    normalized: str
    language: str = "ru"
    is_short: bool = False
    is_specific: bool = False
    fingerprint: str = ""
    n_significant_words: int = 0


class IntentDecision(BaseModel):
    intent_type: IntentTypeLiteral = "discovery"
    categories_pos: list[CategoryLiteral] = []  # pyright: ignore[reportInvalidTypeForm]
    categories_neg: list[CategoryLiteral] = []  # pyright: ignore[reportInvalidTypeForm]
    keywords_pos: list[str] = []
    keywords_neg: list[str] = []
    named_places: list[str] = []
    narrative: list[str] = []
    time_budget_minutes: int | None = None
    era_hint: EraLiteral = "any"
    party_type: PartyTypeLiteral = "solo"
    search_scope: SearchScopeLiteral = "town"


class IntentResult(BaseModel):
    decision: IntentDecision
    source: Literal["agent", "explicit"] = "agent"
    confidence: float = 1.0
    latency_ms: int = 0
    raw_response: dict | None = None


class ResolvedConstraints(BaseModel):
    must_visit_ids: list[int] = []
    resolved_names: list[str] = []
    area_anchor: int | None = None
    optional_categories: list[str] = []
    forbidden_categories: list[str] = []
    forbidden_keywords: list[str] = []
    time_budget_minutes: int | None = 120
    bbox: tuple[float, float, float, float] | None = None
    era_hint: EraLiteral = "any"
    party_type: str = "solo"
    intent_type: str = "discovery"
    must_visit_keywords: list[str] = []
    query_keywords: list[str] = []
    round_trip: bool = False


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
    photo: Photo | None = None
    opening_hours: str | None = None
    ticket_price: str | None = None
    town: str | None = None
    district: str | None = None
    visit_minutes_db: int | None = None
    relevance: float = 0.0
    rrf_score: float = 0.0


class CostMatrix(BaseModel):
    """Walk cost matrix + per-place visit times."""
    walk_seconds: list[list[float]] = []
    visit_minutes: list[int] = []
    indices: list[int] = []


class ValidatedPlan(BaseModel):
    route: list[Candidate]
    walk_seconds: float
    visit_seconds: int
    total_seconds: int
    fits_budget: bool
    stops_dropped: int = 0
    trace: dict = Field(default_factory=dict)


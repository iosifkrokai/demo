"""The wire: what /routes/* accepts and returns."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from core import constants
from routing.models import LatLon, Photo

TransportLiteral = Literal[
    "pedestrian",
    "bicycle",
    "auto",
    "car",
    "truck",
    "bus",
    "motor_scooter",
    "motorcycle",
]
TransportOrAnyLiteral = Literal[
    "pedestrian",
    "bicycle",
    "auto",
    "car",
    "truck",
    "bus",
    "motor_scooter",
    "motorcycle",
    "any",
]

MAX_STOPS = constants.VALHALLA_MAX_LOCATIONS

OverallStatus = Literal["ready", "infeasible", "degraded", "pending"]


class ContextPoint(BaseModel):
    """A stop the already-built route has, sent back on a refinement turn."""

    id: int | None = None
    name: str = Field(max_length=200)
    lat: float
    lon: float
    pinned: bool = False
    source: Literal["agent", "user", "mine"] = "agent"


class RouteContext(BaseModel):
    """The route as it stands plus what the user asks to change about it."""

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
    time_budget_minutes: int | None = Field(default=None, ge=0, le=constants.MAX_BUDGET_MIN)
    origin: LatLon | None = None
    profile: TransportLiteral | None = None
    region_bbox: list[float] | None = Field(
        default=None,
        description="[south, west, north, east]. Used as a PostGIS envelope filter.",
        min_length=4,
        max_length=4,
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
    result_mode: Literal["route", "catalogue"] | None = None
    round_trip: bool = False

    @field_validator("query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be empty")
        return v


class Place(BaseModel):
    id: int
    name: str
    key: str | None = None
    category: str | None
    lat: float
    lon: float
    blurb: str | None = None
    fun_fact: str | None = None
    fun_facts: list[str] = []
    links: list[dict[str, Any]] = []
    photo: Photo | None = None
    visit_minutes: int | None = None
    opening_hours: str | None = None
    ticket_price: str | None = None
    is_free: bool | None = None
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


class StepFreeInfo(BaseModel):
    """What a step-free walk cost, measured against the same stops taken directly."""

    walk_minutes: int
    without_steps_minutes: int
    detour_minutes: int
    length_km: float | None = None
    without_steps_length_km: float | None = None


class RequirementSignal(BaseModel):
    """One requirement as the client renders it — codes only, never prose."""

    kind: Literal["must_visit", "service", "interest", "avoid", "free", "open", "step_free"]
    strength: Literal["hard", "soft"] = "soft"
    code: str | None = None
    name: str | None = None
    origin: Literal["ui", "agent"] = "agent"
    status: Literal["satisfied", "unmet", "uncertain", "pending"] = "pending"
    reason: str | None = None
    place_ids: list[int] = Field(default_factory=list)


class Interpretation(BaseModel):
    """What the system understood — one place, before the plan is judged."""

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
    """A way to actually make the plan, offered when the chosen one does not fit."""

    costing: str
    reason: str
    note: str


class RouteResponse(BaseModel):
    parsed: ParsedQuery
    points: list[Place]
    shape: dict[str, Any]
    summary: RouteSummary
    result_mode: Literal["route", "catalogue"] = "route"
    budget: BudgetInfo | None = None
    explanation: str | None = None
    status: str | None = None
    requirements: list[dict[str, Any]] | None = None
    interpretation: Interpretation | None = None
    costing: str | None = None
    changes: RouteChanges | None = None
    alternatives: list[PlannedAlternative] | None = None
    step_free: StepFreeInfo | None = None
    debug: dict[str, Any] | None = None


class ThreadTurn(BaseModel):
    """One recorded turn: what was asked, what the agent proposed, what was measured."""

    revision: int
    query: str
    submission: dict[str, Any]
    measurement: dict[str, Any]
    created_at: datetime | None = None


class ThreadResponse(BaseModel):
    """GET /routes/threads/{session_id} — the journal of the session that names it."""

    session_id: str
    created_at: datetime | None = None
    turns: list[ThreadTurn] = Field(default_factory=list)


class RerouteStop(BaseModel):
    """One stop the client is showing: a place it found, a point it placed, or both.

    `key` is the client's own name for it, echoed back in the order it was measured.
    """

    key: str = Field(min_length=1, max_length=64)
    place_id: int | None = None
    lat: float | None = None
    lon: float | None = None
    name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _can_be_measured(self) -> RerouteStop:
        if self.place_id is None and (self.lat is None or self.lon is None):
            raise ValueError("a stop needs a place_id, or both a lat and a lon")
        return self


class RerouteReq(BaseModel):
    """POST /routes/reroute body: the route the client holds, to be measured."""

    stops: list[RerouteStop] = Field(default_factory=list, max_length=MAX_STOPS)
    point_ids: list[int] = Field(default_factory=list, max_length=MAX_STOPS)
    profile: TransportLiteral | None = None
    origin: LatLon | None = None
    round_trip: bool = False
    time_budget_minutes: int | None = Field(default=None, ge=0, le=constants.MAX_BUDGET_MIN)
    preserve_order: bool = False

    @model_validator(mode="after")
    def _one_list_of_stops(self) -> RerouteReq:
        if self.stops and self.point_ids:
            raise ValueError("give stops or point_ids, not both")
        if len(self.stops or self.point_ids) < constants.MIN_LEG_POINTS:
            raise ValueError("at least two stops are needed to measure a walk")
        return self

    def as_stops(self) -> list[RerouteStop]:
        """The stops in the client's order, whichever body it sent."""
        if self.stops:
            return self.stops
        return [RerouteStop(key=str(pid), place_id=pid) for pid in self.point_ids]


class ExplainReq(BaseModel):
    point_ids: list[int] = Field(min_length=2, max_length=10)


class ServicesAlongReq(BaseModel):
    """POST /routes/services body: the line the tourist is walking."""

    shape: dict[str, Any] = Field(description="GeoJSON LineString, WGS84")
    profile: TransportOrAnyLiteral | None = None
    categories: list[str] | None = Field(default=None, max_length=8)
    max_off_line_m: float | None = Field(default=None, ge=10, le=1500)
    limit: int | None = Field(default=None, ge=1, le=20)
    session_id: str | None = Field(default=None, max_length=128)


class RouteProgressOut(BaseModel):
    """GET /routes/progress/{id} body: where the run got to."""

    stage: str
    done: bool
    failed: bool
    elapsed_ms: int


class HealthResponse(BaseModel):
    status: Literal["ok", "starting", "degraded"]
    embedder: bool
    llm: bool
    db: bool
    valhalla: bool

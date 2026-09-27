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

from . import constants

# ============================================================================
# HTTP — Requests
# ============================================================================

CategoryLiteral = Literal[
    "замок", "дворец", "усадьба", "костёл", "церковь", "монастырь",
    "храм", "музей", "архитектура", "парк", "памятник",
    "инфраструктура", "кладбище",
    # everyday stops (OSM amenity/tourism POIs) — keep in sync with
    # constants.CATEGORIES
    "кафе", "ресторан", "туалет", "гостиница",
]

EraLiteral = Literal["any", "pre1900", "soviet", "modern"]
IntentTypeLiteral = Literal["discovery", "specific", "themed", "vague"]
PartyTypeLiteral = Literal["solo", "family", "couple", "group"]
# How wide the area of the request is: one town, one rural district around it,
# or the whole administrative region (voblast). Decided by the intent model —
# "все костёлы Гродненской области" is a region, "замки Гродно" is a town.
SearchScopeLiteral = Literal["town", "district", "region"]


class LatLon(BaseModel):
    """Tourist's current position (device geolocation)."""
    lat: float = Field(ge=44.0, le=62.0)   # sane bounds: Belarus ± margins
    lon: float = Field(ge=19.0, le=42.0)


# ── Iterative refinement ("добавь кофейню и туалет") ─────────────────────────


class ContextPoint(BaseModel):
    """A stop the already-built route has, sent back on a refinement turn."""

    id: int | None = None
    name: str = Field(max_length=200)
    lat: float
    lon: float
    # True for stops the user added or kept by hand: a refinement must keep them.
    pinned: bool = False
    # "mine" is the tourist's own position marking the route start.
    source: Literal["agent", "user", "mine"] = "agent"


class RouteContext(BaseModel):
    """The route as it stands plus what the user asks to change about it.

    `instruction` is the delta only ("добавь кофейню и туалет"), not the original
    query: a refinement extracts intent from the delta, while `base_points` are
    forced to survive it.
    """

    instruction: str | None = Field(default=None, max_length=500)
    base_points: list[ContextPoint] = Field(default_factory=list)
    # Stops the user deleted by hand — a refinement must not bring them back.
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
    # The only limit on a route is the time the tourist names. 0 (and a missing
    # field) both mean "без ограничения" — the UI selector's default — so they map
    # to no budget at all instead of a zero-minute one.
    time_budget_minutes: int | None = Field(
        default=None, ge=0, le=constants.MAX_BUDGET_MIN
    )
    # Tourist's current position. When set: geo-focus anchors on it (places near
    # ME, not near the top-scored hit) and the route starts at this point.
    origin: LatLon | None = None
    # Valhalla costing model for the cost matrix + rendered shape.
    # Must match Profile in web-app/src/stores/common-store.ts.
    profile: Literal[
        "pedestrian", "bicycle", "auto", "car", "truck", "bus",
        "motor_scooter", "motorcycle",
    ] | None = None
    region_bbox: list[float] | None = Field(
        default=None,
        description="[south, west, north, east]. Used as a PostGIS envelope filter.",
        min_length=4, max_length=4,
    )
    allow_auto_relax: bool = Field(default=True)
    conversation_id: str | None = Field(default=None)
    # Present on a refinement turn: the route as it stands (base_points), the
    # stops the user deleted (excluded_ids) and the delta instruction. Absent on
    # a first turn — and then the pipeline behaves exactly as before this field
    # existed.
    context: RouteContext | None = None
    user_interests: list[str] | None = Field(default=None)
    preferences: dict | None = Field(default=None)

    # ── Explicit filters (spec 002) ─────────────────────────────────────────
    # Anything the tourist set with a visible control. Explicit values win over
    # a guess made from `query`; a genuine conflict is a clarification, not a
    # silent override.
    locale: Literal["ru", "en"] = "ru"
    party_adults: int | None = Field(default=None, ge=0, le=50)
    # Count only. Ages are never invented: an age the user did not name stays
    # absent and the route must not pretend to know it.
    party_children: int | None = Field(default=None, ge=0, le=20)
    party_children_ages: list[int] = Field(default_factory=list)
    mobility: list[str] = Field(default_factory=list)
    # Category codes the route MUST serve ("туалет" = a toilet is mandatory).
    hard_services: list[str] = Field(default_factory=list)
    # Category codes the tourist would like more of (soft).
    interests: list[str] = Field(default_factory=list)
    # Category codes to keep out of the route.
    avoid: list[str] = Field(default_factory=list)
    # "route" = an itinerary; "catalogue" = a grouped list of places to choose from.
    result_mode: Literal["route", "catalogue"] = "route"
    round_trip: bool = False

    @field_validator("query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be empty")
        return v


class RerouteReq(BaseModel):
    point_ids: list[int] = Field(min_length=2, max_length=10)
    profile: Literal[
        "pedestrian", "bicycle", "auto", "car", "truck", "bus",
        "motor_scooter", "motorcycle",
    ] | None = None


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
    opening_hours: str | None = None
    ticket_price: str | None = None
    town: str | None = None
    district: str | None = None


class ParsedQuery(BaseModel):
    keywords: list[str] = []
    categories: list[str] = []
    time_budget_minutes: int | None = None
    # Where the requirement reading came from: "agent" (the PydanticAI
    # interpretation layer), "llm" (a model reading merged with UI filters),
    # "fallback" (the deterministic parser), "explicit" (UI filters only),
    # "regex" (the deterministic intent parse). The retired "gemini"/"jev"
    # values are gone with their producers.
    source: Literal["agent", "llm", "fallback", "explicit", "regex"] = "fallback"


class BudgetInfo(BaseModel):
    # None = the user stated no time limit; the route is not trimmed to fit one.
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

    The client localises `code` (and `reason`) itself; nothing here is a
    sentence in any language.  `origin` is the honest provenance of that single
    line: a visible UI control, the interpretation model, or the deterministic
    parser.
    """

    kind: Literal["must_visit", "service", "interest", "avoid"]
    strength: Literal["hard", "soft"] = "soft"
    # Canonical taxonomy code for service/interest/avoid; None for must_visit.
    code: str | None = None
    # The proper noun for a must_visit requirement ("Фарный костёл").
    name: str | None = None
    # "ui" = a visible control, "agent" = the interpretation model, "fallback"
    # = the deterministic parser (no key / the model could not answer).
    origin: Literal["ui", "agent", "fallback"] = "fallback"
    status: Literal["satisfied", "unmet", "uncertain", "pending"] = "pending"
    # verify.py's machine-readable reason code ("hard_service_absent", ...).
    reason: str | None = None
    # The stops that satisfied it.
    place_ids: list[int] = Field(default_factory=list)


class Interpretation(BaseModel):
    """What the system understood — one place, before the plan is judged.

    The client renders `requirements` as chips (with `unmet` called out) and
    shows `source` so the user knows whether a model or the deterministic
    parser read their text.  Every field is a code or a number; the Russian/
    English wording is the client's.
    """

    # "agent"/"mixed" = the interpretation model read the text, "fallback" =
    # the deterministic parser answered, "explicit" = only UI filters.
    source: Literal["llm", "mixed", "explicit", "fallback"] = "fallback"
    locale: Literal["ru", "en"] = "ru"
    # Overall fate, from verify.py::overall_status — not from the model.
    status: Literal["ready", "infeasible", "degraded", "pending"] = "pending"

    adults: int | None = None
    children: int | None = None
    children_ages: list[int] = Field(default_factory=list)
    mobility: list[str] = Field(default_factory=list)
    budget_minutes: int | None = None
    # Resolved area slugs, e.g. ["grodno-old-town"].
    areas: list[str] = Field(default_factory=list)
    # Valhalla costing / transport of the plan ("pedestrian", "bicycle").
    transport: str | None = None
    result_mode: Literal["route", "catalogue"] = "route"
    round_trip: bool = False

    requirements: list[RequirementSignal] = Field(default_factory=list)
    # Every requirement that is NOT proven satisfied, with its reason code.
    # Non-empty here is the explicit signal that something the user asked for
    # is missing from the plan — never a silent success.
    unmet: list[RequirementSignal] = Field(default_factory=list)
    # Asks this system cannot represent or prove ("step_free").
    unknowns: list[str] = Field(default_factory=list)


class RouteResponse(BaseModel):
    parsed: ParsedQuery
    points: list[Place]
    shape: dict
    summary: RouteSummary
    budget: BudgetInfo | None = None
    explanation: str | None = None
    # Overall fate of the request, decided by the deterministic verifier
    # (planner/verify.py) against the final route + geometry — never by the
    # interpretation model: "ready" | "infeasible" | "degraded" | "pending".
    status: str | None = None
    # Per-requirement verdicts (kind/strength/code/status/place_ids). The
    # verification of a mandatory requirement is visible here, and the status
    # above is derived from these.
    requirements: list[dict] | None = None
    # What the system understood, in one place, for the client to render before
    # and alongside the plan (chips + the explicit unmet list).
    interpretation: Interpretation | None = None
    # Valhalla costing the plan and geometry were built with ("pedestrian",
    # "auto", ...). The webapp mirrors it into its own profile so the line it
    # draws itself uses the same transport as the plan.
    costing: str | None = None
    # Filled in on a refinement turn: what changed vs the route the user had.
    changes: RouteChanges | None = None
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
    search_scope: SearchScopeLiteral = "town"


class IntentResult(BaseModel):
    decision: IntentDecision
    source: Literal["agent", "regex", "fallback"] = "regex"
    confidence: float = 1.0
    latency_ms: int = 0
    raw_response: dict | None = None


# ============================================================================
# Internal planner — Step 2 (constraint resolution)
# ============================================================================

class ResolvedConstraints(BaseModel):
    must_visit_ids: list[int] = []
    area_anchor: int | None = None  # town/district-only geo anchor, not a POI
    optional_categories: list[str] = []
    forbidden_categories: list[str] = []
    forbidden_keywords: list[str] = []
    time_budget_minutes: int | None = 120
    bbox: tuple[float, float, float, float] | None = None  # (W, S, E, N)
    era_hint: EraLiteral = "any"
    party_type: str = "solo"
    intent_type: str = "discovery"
    must_visit_keywords: list[str] = []
    query_keywords: list[str] = []


# ============================================================================
# Internal planner — Steps 3-4 (retrieval / MMR)
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
    opening_hours: str | None = None
    ticket_price: str | None = None
    town: str | None = None
    district: str | None = None
    visit_minutes_db: int | None = None  # curated visit time from the region dataset
    relevance: float = 0.0          # higher is better
    rrf_score: float = 0.0          # RRF signal strength


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


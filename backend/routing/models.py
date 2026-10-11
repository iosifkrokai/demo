"""The measurement currency — what the deterministic layer speaks.

This lives below both the planner and the agent so the agent's tools can call the
routing math without either of them importing the other. It is the *measurement*
half of the old ``planner.models``; the HTTP wire and the pipeline glue stayed
behind.

``Candidate`` still carries display fields (blurb, links, photo). That is
transitional: once the response is assembled from ids at the edge, they collapse
into a thin ``Stop`` and leave this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

EraLiteral = Literal["any", "pre1900", "soviet", "modern"]
IntentTypeLiteral = Literal["discovery", "specific", "themed", "vague"]
PartyTypeLiteral = Literal["solo", "family", "couple", "group"]
SearchScopeLiteral = Literal["town", "district", "region"]


class LatLon(BaseModel):
    """Tourist's current position (device geolocation)."""

    lat: float = Field(ge=44.0, le=62.0)
    lon: float = Field(ge=19.0, le=42.0)


@dataclass(frozen=True)
class CategoryFacts:
    """What the measurement needs to know about categories — and nothing else.

    The taxonomy lives in the database, but the arithmetic here is a leaf: it is
    handed this value rather than a repository, so the cost matrix can be computed
    from coordinates and numbers alone, with no connection in reach.
    """

    codes: frozenset[str] = field(default_factory=frozenset)
    visit_minutes: Mapping[str, int] = field(default_factory=dict)
    roles: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, categories: Any) -> CategoryFacts:
        """Build from taxonomy rows (anything with code/ru/visit_minutes/role).

        `places.category` stores the Russian name, not the code, so both surfaces
        are keyed — a lookup succeeds whichever one the data carries.
        """
        codes: set[str] = set()
        minutes: dict[str, int] = {}
        roles: dict[str, str] = {}
        for row in categories:
            codes.add(row.code)
            for surface in (row.code, row.ru):
                minutes[surface] = row.visit_minutes
                roles[surface] = row.role
        return cls(codes=frozenset(codes), visit_minutes=minutes, roles=roles)

    def minutes_for(self, category: str | None) -> int | None:
        """Default visit time for a category, or None when it is unknown."""
        if not category:
            return None
        return self.visit_minutes.get(category)

    def is_service(self, category: str | None) -> bool:
        if not category:
            return False
        return self.roles.get(category) == "service"


class Photo(BaseModel):
    """A picture of a point, always with the credit the licence demands.

    url, author, license and source travel together; incomplete records are never emitted.
    """

    url: str
    author: str
    license: str
    source: str | None = None


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
    links: list[dict[str, Any]] = []
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
    trace: dict[str, Any] = Field(default_factory=dict)


ResultMode = Literal["route", "catalogue"]

# --- what the reader understood ----------------------------------------------

RequirementKind = Literal["must_visit", "service", "interest", "avoid"]

REASON_MUST_VISIT_OUTSIDE = "must_visit_outside_coverage"

Strength = Literal["hard", "soft"]

RequirementStatus = Literal["pending", "satisfied", "unmet", "uncertain"]

RequirementSource = Literal["text", "ui"]


class Requirement(BaseModel):
    """One thing the tourist asked for, with its provenance and its fate."""

    kind: RequirementKind
    strength: Strength = "soft"

    code: str | None = None

    label: str | None = None

    text: str | None = None

    name: str | None = None
    place_id: int | None = None

    source: RequirementSource = "text"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    status: RequirementStatus = "pending"
    place_ids: list[int] = Field(default_factory=list)
    reason: str | None = None

    def is_resolved(self) -> bool:
        return self.status in ("satisfied", "unmet", "uncertain")


class PartyComposition(BaseModel):
    """Who is walking. Only facts the user stated — ages are never invented."""

    adults: int | None = Field(default=None, ge=0, le=50)
    children: int | None = Field(default=None, ge=0, le=20)
    children_ages: list[int] = Field(default_factory=list)
    mobility: list[str] = Field(default_factory=list)


class TripRequirements(BaseModel):
    """Everything the route must respect, in one place.

    Built once per request, rebuilt from the delta on a refinement turn.
    """

    locale: Literal["ru", "en"] = "ru"
    raw_query: str = ""

    party: PartyComposition = Field(default_factory=PartyComposition)

    budget_minutes: int | None = None

    costing: str | None = None

    origin_lat: float | None = None
    origin_lon: float | None = None

    areas: list[str] = Field(default_factory=list)

    outside_coverage: list[str] = Field(default_factory=list)

    result_mode: ResultMode = "route"
    round_trip: bool = False

    requirements: list[Requirement] = Field(default_factory=list)

    unknowns: list[str] = Field(default_factory=list)

    source: Literal["llm", "explicit", "mixed"] = "llm"

    def hard(self) -> list[Requirement]:
        return [r for r in self.requirements if r.strength == "hard"]

    def soft(self) -> list[Requirement]:
        return [r for r in self.requirements if r.strength == "soft"]

    def of_kind(self, kind: RequirementKind) -> list[Requirement]:
        return [r for r in self.requirements if r.kind == kind]

    def hard_service_codes(self) -> list[str]:
        """Category codes that MUST appear on the route."""
        return [r.code for r in self.hard() if r.kind == "service" and r.code]

    def soft_service_codes(self) -> list[str]:
        return [r.code for r in self.soft() if r.kind == "service" and r.code]

    def must_visit_ids(self) -> list[int]:
        return [r.place_id for r in self.of_kind("must_visit") if r.place_id is not None]

    def must_visit_names(self) -> list[str]:
        return [r.name for r in self.of_kind("must_visit") if r.name]

    def avoid_codes(self) -> list[str]:
        return [r.code for r in self.of_kind("avoid") if r.code]

    def interest_codes(self) -> list[str]:
        return [r.code for r in self.of_kind("interest") if r.code]

    def unresolved_hard(self) -> list[Requirement]:
        """Hard requirements still pending — the route cannot be reported ready."""
        return [r for r in self.hard() if r.status == "pending"]

    def failed_hard(self) -> list[Requirement]:
        """Hard requirements the verifier could not satisfy or prove."""
        return [r for r in self.hard() if r.status in ("unmet", "uncertain")]

    def is_ready(self) -> bool:
        """True only when every hard requirement is proven satisfied."""
        return not self.failed_hard() and not self.unresolved_hard()

    def public_requirements(self) -> list[dict[str, Any]]:
        """Localizable view of the requirement list for the HTTP response.

        Frontend renders from kind/code/status so both locales share one contract.
        """
        return [
            {
                "kind": r.kind,
                "strength": r.strength,
                "code": r.code,
                "status": r.status,
                "place_ids": list(r.place_ids),
                "detail": r.reason,
            }
            for r in self.requirements
        ]

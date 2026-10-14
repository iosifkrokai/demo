"""The measurement currency — what the deterministic layer speaks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class LatLon(BaseModel):
    """Tourist's current position (device geolocation)."""

    lat: float = Field(ge=44.0, le=62.0)
    lon: float = Field(ge=19.0, le=42.0)


@dataclass(frozen=True)
class CategoryFacts:
    """What the measurement needs to know about categories — and nothing else."""

    codes: frozenset[str] = field(default_factory=frozenset)
    visit_minutes: Mapping[str, int] = field(default_factory=dict)
    roles: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, categories: Any) -> CategoryFacts:
        """Build from taxonomy rows (anything with code/ru/visit_minutes/role)."""
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
    """A picture of a point, always with the credit the licence demands."""

    url: str
    author: str
    license: str
    source: str | None = None


class ResolvedConstraints(BaseModel):
    must_visit_ids: list[int] = []
    time_budget_minutes: int | None = 120
    round_trip: bool = False
    origin: LatLon | None = None


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


class OriginAnchor(BaseModel):
    """The tourist's own position as a leg endpoint, measured beside the matrix
    rather than as a row of it: the origin is not a stop and has no index."""

    from_origin: list[float] = []
    to_origin: list[float] = []


class CostMatrix(BaseModel):
    """Walk cost matrix + per-place visit times, and the origin's own legs."""

    walk_seconds: list[list[float]] = []
    visit_minutes: list[int] = []
    indices: list[int] = []
    anchor: OriginAnchor | None = None


@dataclass(frozen=True)
class WalkRequest:
    """What the tourist asked to walk, before there is a matrix to measure it on."""

    stop_ids: list[int]
    profile: str = "pedestrian"
    budget_minutes: int | None = None
    origin: LatLon | None = None
    round_trip: bool = False
    must_keep_ids: list[int] | None = None
    locale: str = "ru"

    def constraints(self, *, keep: list[int] | None = None) -> ResolvedConstraints:
        """This request, as the rules the routing layer reads."""
        return ResolvedConstraints(
            must_visit_ids=list(self.must_keep_ids or [] if keep is None else keep),
            time_budget_minutes=self.budget_minutes,
            round_trip=self.round_trip,
            origin=self.origin,
        )


@dataclass(frozen=True)
class WalkProblem:
    """One measurement's whole input: the stops, the matrix, and the rules."""

    candidates: list[Candidate]
    cost: CostMatrix
    constraints: ResolvedConstraints
    costing: str = "pedestrian"

    @property
    def matrix(self) -> list[list[float]]:
        return self.cost.walk_seconds

    @property
    def visits(self) -> list[int]:
        return self.cost.visit_minutes

    @property
    def anchor(self) -> OriginAnchor | None:
        return self.cost.anchor

    @property
    def round_trip(self) -> bool:
        return self.constraints.round_trip

    @property
    def budget_s(self) -> int | None:
        minutes = self.constraints.time_budget_minutes
        return minutes * 60 if minutes else None

    @property
    def must_idx(self) -> list[int]:
        wanted = set(self.constraints.must_visit_ids)
        return [i for i, c in enumerate(self.candidates) if c.id in wanted]


class ValidatedPlan(BaseModel):
    route: list[Candidate]
    walk_seconds: float
    visit_seconds: int
    total_seconds: int
    fits_budget: bool
    stops_dropped: int = 0
    trace: dict[str, Any] = Field(default_factory=dict)


ResultMode = Literal["route", "catalogue"]


RequirementKind = Literal["must_visit", "service", "interest", "avoid", "free", "open"]

CODE_LESS_KINDS: frozenset[str] = frozenset({"free", "open"})

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
    """Everything the route must respect, in one place."""

    locale: Literal["ru", "en"] = "ru"
    raw_query: str = ""

    at: datetime | None = None

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
        """Localizable view of the requirement list for the HTTP response."""
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

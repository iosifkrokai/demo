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

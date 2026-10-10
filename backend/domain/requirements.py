"""Frozen request-semantics contract: what the tourist asked for.

The single typed vocabulary shared by planner, retrieval, optimizer and HTTP.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RequirementKind = Literal["must_visit", "service", "interest", "avoid"]

REASON_MUST_VISIT_OUTSIDE = "must_visit_outside_coverage"

Strength = Literal["hard", "soft"]

RequirementStatus = Literal["pending", "satisfied", "unmet", "uncertain"]

RequirementSource = Literal["text", "ui"]

ResultMode = Literal["route", "catalogue"]


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

    source: Literal["llm", "explicit", "fallback", "mixed"] = "fallback"

    def hard(self) -> list[Requirement]:
        return [r for r in self.requirements if r.strength == "hard"]

    def soft(self) -> list[Requirement]:
        return [r for r in self.requirements if r.strength == "soft"]

    def of_kind(self, kind: RequirementKind) -> list[Requirement]:
        return [r for r in self.requirements if r.kind == kind]

    def hard_service_codes(self) -> list[str]:
        """Category codes that MUST appear on the route."""
        return [
            r.code for r in self.hard() if r.kind == "service" and r.code
        ]

    def soft_service_codes(self) -> list[str]:
        return [
            r.code for r in self.soft() if r.kind == "service" and r.code
        ]

    def must_visit_ids(self) -> list[int]:
        return [
            r.place_id for r in self.of_kind("must_visit") if r.place_id is not None
        ]

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

    def public_requirements(self) -> list[dict]:
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

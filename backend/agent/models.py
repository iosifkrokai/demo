"""The contracts the interpretation layer speaks.

Two directions, two shapes, both owned here:

* ``ReaderBrief`` — what the planner tells the reader about the request. Narrower
  than the HTTP body on purpose: the reader never sees a client id or a raw JSON
  object, only the facts that reach a prompt or a tool call.
* ``TripRequirements`` — what the reader understood. The planner consumes it, the
  verifier grades against it, the HTTP response is rendered from it.

Defining them here is what keeps this package a leaf: it imports no planner and
no HTTP model, so the pipeline can drive it without a cycle.

(``Requirement``/``TripRequirements`` were ``domain.requirements`` until the model
layer was split up; the words did not change, only the address.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

# The brief speaks the same closed set for the mode it is told to plan in.
ResultMode = Literal["route", "catalogue"]


# --- what the planner tells the reader ---------------------------------------


@dataclass(frozen=True)
class BriefStop:
    """A stop already on the route, as the reader is told about it."""

    place_id: int | None
    name: str
    pinned: bool = False


@dataclass(frozen=True)
class RefinementBrief:
    """What the tourist asked to change about the route they already hold."""

    instruction: str | None = None
    revision: int = 0
    excluded_ids: tuple[int, ...] = ()
    base_points: tuple[BriefStop, ...] = ()


@dataclass(frozen=True)
class ReaderBrief:
    """The request as far as the reader is concerned.

    The controls are the user's visible choices: they are applied with higher
    precedence than the reading, so the reader is told them rather than asked to
    guess them. Ages are carried, never invented.
    """

    locale: Literal["ru", "en"] = "ru"
    profile: str | None = None
    origin: tuple[float, float] | None = None
    time_budget_minutes: int | None = None
    party_adults: int | None = None
    party_children: int | None = None
    party_children_ages: tuple[int, ...] = ()
    mobility: tuple[str, ...] = ()
    hard_services: tuple[str, ...] = ()
    interests: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    result_mode: ResultMode = "route"
    round_trip: bool = False
    context: RefinementBrief | None = None


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

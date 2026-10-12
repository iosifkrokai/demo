"""The shapes the interpretation agent speaks in.

The model's structured output (``AgentReading``/``AgentRequirement``) and the
dependencies a single run carries (``PlanningDeps``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from routing.models import CategoryFacts, LatLon

if TYPE_CHECKING:
    from db.store.registry import Repositories

RequirementKindL = Literal["must_visit", "service", "interest", "avoid"]
StrengthL = Literal["hard", "soft"]


class AgentRequirement(BaseModel):
    """One requirement the model read out of the text."""

    kind: RequirementKindL
    strength: StrengthL = "soft"
    code: str | None = None
    name: str | None = None
    text: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    place_id: int | None = None


class PlanSubmission(BaseModel):
    """Everything the agent says, in one shape: what it understood, and what it proposes.

    One shape because the run ends once. Reading and planning are the same
    decision — a stop can only be chosen once its name has been resolved — and
    splitting them would let a plan arrive with no statement of what was asked
    for.
    """

    # --- what was understood ---
    requirements: list[AgentRequirement] = Field(default_factory=list)
    adults: int | None = Field(default=None, ge=0, le=50)
    children: int | None = Field(default=None, ge=0, le=20)
    children_ages: list[int] = Field(default_factory=list)
    mobility: list[str] = Field(default_factory=list)
    budget_minutes: int | None = None
    areas: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    outside_coverage: list[str] = Field(default_factory=list)

    # --- what is proposed ---
    # The outcome is explicit so "this request cannot be served" is expressible
    # rather than smuggled through an empty stop list. `route` and `catalogue`
    # pick the shape of the answer (a walk, or a list to choose from); `refuse`
    # says the request cannot be planned at all — a place outside the served
    # region — and carries the reason in `refusal_reason`. None means "no answer
    # of your own", and the brief's own result_mode then stands.
    outcome: Literal["route", "catalogue", "refuse"] | None = None
    refusal_reason: str | None = None
    ordered_ids: list[int] = Field(default_factory=list)
    narrative: str = ""
    profile: str = "pedestrian"
    round_trip: bool = False
    must_keep_ids: list[int] = Field(default_factory=list)


@dataclass
class PlanningDeps:
    """What one planning run may reach, and what it already knows.

    ``observed_ids`` guards every id the model may speak: it may only reference a
    place the tools actually handed it. ``deadline_at`` is the run's end on the
    monotonic clock, so a tool can refuse to start work it cannot finish.
    """

    repos: Repositories | None = None
    observed_ids: set[int] = field(default_factory=set)

    locale: str = "ru"
    origin: LatLon | None = None
    facts: CategoryFacts = field(default_factory=CategoryFacts)
    deadline_at: float | None = None
    # The request's own territory, as [south, west, north, east]: every
    # `find_places` call the run makes is scoped to it unless the model names a
    # territory of its own.
    region_bbox: tuple[float, float, float, float] | None = None

    def seconds_left(self) -> float:
        """Seconds until this run's deadline; infinite when it has none."""
        if self.deadline_at is None:
            return float("inf")
        return self.deadline_at - time.monotonic()

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


class AgentReading(BaseModel):
    """Everything the agent is allowed to say about one request."""

    requirements: list[AgentRequirement] = Field(default_factory=list)
    adults: int | None = Field(default=None, ge=0, le=50)
    children: int | None = Field(default=None, ge=0, le=20)
    children_ages: list[int] = Field(default_factory=list)
    mobility: list[str] = Field(default_factory=list)
    budget_minutes: int | None = None
    areas: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    result_mode: Literal["route", "catalogue"] | None = None
    outside_coverage: list[str] = Field(default_factory=list)


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

    def seconds_left(self) -> float:
        """Seconds until this run's deadline; infinite when it has none."""
        if self.deadline_at is None:
            return float("inf")
        return self.deadline_at - time.monotonic()

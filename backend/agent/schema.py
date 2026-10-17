"""The shapes the interpretation agent speaks in."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from routing.models import CategoryFacts, LatLon, RequirementKind

if TYPE_CHECKING:
    from db.store.registry import Repositories

StrengthL = Literal["hard", "soft"]


class AgentRequirement(BaseModel):
    """One requirement the model read out of the text."""

    kind: RequirementKind = Field(
        description=(
            "must_visit — a named place to SEE; interest — a category of place "
            "(«замки»); service — a café, toilet or hotel the walk passes; avoid — "
            "a class the user ruled out («без храмов»); free, open and step_free — "
            "a condition with no code, checked by the code against the stops."
        )
    )
    strength: StrengthL = Field(
        default="soft",
        description=(
            "'hard' when the user made it mandatory («обязательно») or ruled it out "
            "(«без …», «не хочу …», «кроме …»); 'soft' for a wish. A ban is never soft."
        ),
    )
    code: str | None = Field(
        default=None,
        description=(
            "One canonical code from the catalogue in the instructions — never a word "
            "of your own. A ban carries one requirement per code its class spans: "
            "«без храмов» is костёл + церковь + храм, not храм alone."
        ),
    )
    name: str | None = None
    text: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    place_id: int | None = None


class PlanSubmission(BaseModel):
    """Everything the agent says, in one shape: what it understood, and what it proposes."""

    requirements: list[AgentRequirement] = Field(default_factory=list)
    adults: int | None = Field(default=None, ge=0, le=50)
    children: int | None = Field(default=None, ge=0, le=20)
    children_ages: list[int] = Field(default_factory=list)
    mobility: list[str] = Field(default_factory=list)
    budget_minutes: int | None = None
    areas: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    outside_coverage: list[str] = Field(default_factory=list)

    outcome: Literal["route", "catalogue", "refuse"] | None = None
    refusal_reason: str | None = None
    ordered_ids: list[int] = Field(
        default_factory=list, description="The stops of the walk, in the order they are walked."
    )
    narrative: str = ""
    profile: str = "pedestrian"
    round_trip: bool = False
    must_keep_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Ids of places the tourist asked to go INTO. A service here becomes a stop "
            "of the walk; every other service is something the walk passes."
        ),
    )


@dataclass
class PlanningDeps:
    """What one planning run may reach, and what it already knows."""

    repos: Repositories | None = None
    observed_ids: set[int] = field(default_factory=set)
    tool_calls_used: int = 0
    instructions: str | None = None

    locale: str = "ru"
    origin: LatLon | None = None
    facts: CategoryFacts = field(default_factory=CategoryFacts)
    deadline_at: float | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    at_chosen: bool = False

    def seconds_left(self) -> float:
        """Seconds until this run's deadline; infinite when it has none."""
        if self.deadline_at is None:
            return float("inf")
        return self.deadline_at - time.monotonic()

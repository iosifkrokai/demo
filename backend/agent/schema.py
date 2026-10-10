"""The shapes the interpretation agent speaks in.

The model's structured output (``AgentReading``/``AgentRequirement``) and the
dependencies a single run carries (``InterpretDeps``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

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
class InterpretDeps:
    """``observed_ids`` guards `place_id`: the model may only reference a place
    the tools actually handed it.
    """

    db: Any | None = None
    observed_ids: set[int] = field(default_factory=set)

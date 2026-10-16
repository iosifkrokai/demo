"""The contracts the interpretation layer speaks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from routing.models import (
    REASON_MUST_VISIT_OUTSIDE,
    PartyComposition,
    Requirement,
    RequirementKind,
    RequirementSource,
    RequirementStatus,
    ResultMode,
    Strength,
    TripRequirements,
)

__all__ = [
    "REASON_MUST_VISIT_OUTSIDE",
    "BriefStop",
    "PartyComposition",
    "ReaderBrief",
    "RefinementBrief",
    "Requirement",
    "RequirementKind",
    "RequirementSource",
    "RequirementStatus",
    "ResultMode",
    "Strength",
    "TripRequirements",
]


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
    """The request as far as the reader is concerned."""

    locale: Literal["ru", "en"] = "ru"
    profile: str | None = None
    at: datetime | None = None
    origin: tuple[float, float] | None = None
    time_budget_minutes: int | None = None
    party_adults: int | None = None
    party_children: int | None = None
    party_children_ages: tuple[int, ...] = ()
    mobility: tuple[str, ...] = ()
    hard_services: tuple[str, ...] = ()
    interests: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    result_mode: ResultMode | None = None
    round_trip: bool = False
    context: RefinementBrief | None = None

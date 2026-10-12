"""The contracts the interpretation layer speaks.

Two directions, two shapes, both owned here:

* ``ReaderBrief`` — what the planner tells the reader about the request. Narrower
  than the HTTP body on purpose: the reader never sees a client id or a raw JSON
  object, only the facts that reach a prompt or a tool call.
* ``TripRequirements`` — what the reader understood. The planner consumes it, the
  verifier grades against it, the HTTP response is rendered from it.

Defining them here is what keeps this package a leaf: it imports no planner and
no HTTP model, so the pipeline can drive it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# The requirement contract is shared: the reader produces it, the verifier grades
# against it, and the response renders it. It lives below both.
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

# The brief speaks the same closed set for the mode it is told to plan in.


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
    region_bbox: tuple[float, float, float, float] | None = None
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

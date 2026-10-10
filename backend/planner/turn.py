"""The per-request state the pipeline threads through its steps.

One `Pipeline` serves every request, so anything request-scoped has to live
somewhere else. It lives here: the request, when it started, what the steps
decided, and the pool as it was trimmed.

The steps used to be methods on `Pipeline`, which meant passing this object to
each of them from the outside. Now that they are functions, it is the only thing
they share.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from contracts.planner import Candidate, GenerateReq
from core import constants


@dataclass
class Turn:
    """One request: what it asked, when it started, and what the steps decided."""

    req: GenerateReq
    t0: float

    region_scope: bool = False
    catalogue: bool = False
    refuse: list[str] | None = None

    qvec: list[float] = field(default_factory=list)
    near: tuple[float, float] | None = None
    excluded: set[int] = field(default_factory=set)
    costing: str = "pedestrian"
    round_trip: bool = False

    all_candidates: list[Candidate] = field(default_factory=list)
    plan_info: dict[str, Any] | None = None
    shape: dict = field(default_factory=dict)
    walk_s: float = 0.0
    length_km: float | None = None
    base_candidates: list[Candidate] = field(default_factory=list)

    deadline_trim: int | None = None
    deadline_order_skipped: bool = False
    deadline_geometry_skipped: bool = False

    # "generate" | "refine" | "reroute" — which tail the plan takes.
    tail_mode: str = "generate"


def seconds_left(t0: float) -> float:
    """Seconds left of this request's end-to-end deadline (may be < 0)."""
    return constants.REQUEST_DEADLINE_S - (time.perf_counter() - t0)

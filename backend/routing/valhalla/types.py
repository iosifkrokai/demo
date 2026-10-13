"""Route status/result types and the time sentinels."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from core import constants


class RouteStatus(Enum):
    USABLE = "usable"
    NO_ROUTE_EXISTS = "no_route_exists"
    SERVICE_UNAVAILABLE = "service_unavailable"
    EMPTY_GEOMETRY = "empty_geometry"


@dataclass
class RouteResult:
    status: RouteStatus
    shape: dict[str, Any]
    summary: dict[str, Any] | None
    maneuvers: list[dict[str, Any]] | None = None
    language: str | None = None


def is_unreachable_time(seconds: float) -> bool:
    """Check if a time value represents an unreachable pair."""
    return seconds == float(constants.UNREACHABLE_S)


def is_unknown_time(seconds: float) -> bool:
    """True when the walk time was never measured (a transport failure)."""
    return isinstance(seconds, float) and math.isnan(seconds)

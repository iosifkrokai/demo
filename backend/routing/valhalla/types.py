"""Route status/result types and the time sentinels."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class RouteStatus(Enum):
    USABLE = "usable"
    PARTIAL = "partial"
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

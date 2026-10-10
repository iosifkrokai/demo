"""``services_near_route`` — services within a detour of a route shape."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.model import RunContext
from agent.schema import InterpretDeps
from agent.tools import ERR_NOT_IMPLEMENTED, _clamp, _envelope
from domain.taxonomy import db_values

MAX_SERVICES_PER_CALL = 5
MAX_ROUTE_POINTS = 64
MAX_DETOUR_MINUTES = 30


def services_near_route(
    category_codes: list[str] | None = None,
    shape: list | None = None,
    max_detour_minutes: float | None = None,
) -> dict:
    """NOT IMPLEMENTED — a deliberate gap: it answers ``error="not_implemented"``
    with an empty result set, so the agent treats the service as unproven.
    """
    codes = [c for c in (category_codes or []) if isinstance(c, str) and c.strip()]
    db_cats = db_values(codes)
    points = [p for p in (shape or []) if p is not None]
    detour, detour_clamped = _clamp(max_detour_minutes, 1, MAX_DETOUR_MINUTES, MAX_DETOUR_MINUTES)

    provenance: dict[str, Any] = {
        "source": "places+valhalla",
        "reason_code": ERR_NOT_IMPLEMENTED,
        "reason": "needs a real Valhalla route along `shape` to measure a detour",
        "result_cap": MAX_SERVICES_PER_CALL,
        "categories": db_cats,
        "dropped_codes": [c for c in codes if c not in db_cats],
        "points_in": len(points),
        "max_detour_minutes": int(detour),
        "detour_clamped": detour_clamped,
    }

    return _envelope(
        "services_near_route",
        [],
        provenance,
        capped=bool(len(points) > MAX_ROUTE_POINTS or detour_clamped),
        error=ERR_NOT_IMPLEMENTED,
        message=(
            "detour measurement is not implemented (no Valhalla route along the shape); "
            "the presence of a service cannot be confirmed by this tool"
        ),
    )


_services_near_route = services_near_route


def register_services_near_route(agent: Any, remember: Callable[[Any, dict], dict]) -> None:
    """Advertise ``services_near_route`` to the agent."""

    @agent.tool
    def services_near_route(
        ctx: RunContext[InterpretDeps],
        category_codes: list[str] | None = None,
        shape: list | None = None,
        max_detour_minutes: float | None = None,
    ) -> dict:
        """Services within a detour of a route shape (NOT implemented — honest gap)."""
        return _services_near_route(category_codes, shape, max_detour_minutes)

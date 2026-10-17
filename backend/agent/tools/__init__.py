"""Bounded tool surface for the interpretation agent."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.tools import areas, places, plan, services, verify

TOOL_REGISTRARS: tuple[Callable[[Any], None], ...] = (
    areas.register_find_areas,
    places.register_find_places,
    plan.register_plan_walk,
    places.register_get_place_facts,
    verify.register_check_requirements,
    services.register_nearby_services,
)


def register_all(agent: Any) -> None:
    """Advertise every tool in the registry on ``agent``."""
    for register in TOOL_REGISTRARS:
        register(agent)

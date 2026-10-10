"""Bounded tool surface for the interpretation agent.

The registry lives here: one line per tool the agent advertises. Adding a tool
means a new module under ``tools/`` plus one entry in :data:`TOOL_REGISTRARS`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

ERR_EMPTY_QUERY = "empty_query"
ERR_BAD_ARGUMENT = "bad_argument"
ERR_NOT_FOUND = "not_found"
ERR_DB_UNAVAILABLE = "db_unavailable"
ERR_AREA_REGISTRY = "area_registry_unavailable"
ERR_NOT_IMPLEMENTED = "not_implemented"


def _envelope(
    tool: str,
    results: list[dict],
    provenance: dict,
    *,
    capped: bool = False,
    error: str | None = None,
    message: str | None = None,
) -> dict:
    """One shape for every tool answer: data + provenance + honest statuses."""
    return {
        "results": results,
        "count": len(results),
        "capped": capped,
        "error": error,
        "message": message,
        "provenance": {"tool": tool, **provenance},
    }


def _clamp(
    value: Any, low: int | float, high: int | float, default: int | float
) -> tuple[int | float, bool]:
    """Clamp a number the model sent into [low, high]; report whether we did.

    A missing/unparseable value becomes `default`, not an error.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return default, False
    if value < low:
        return low, value != low
    if value > high:
        return high, True
    return value, False


def _project(row: dict, fields: tuple[str, ...]) -> dict:
    return {f: row.get(f) for f in fields if f in row}


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _norm_term(text: str) -> str:
    """Lowercase, ё-folded, whitespace-collapsed — for term matching only."""
    return " ".join(str(text).lower().replace("ё", "е").split())


# The submodule imports sit below the shared helpers they reach back for.
from agent.tools import (  # noqa: E402
    _db,
    areas,
    places,
    services,
)
from agent.tools._db import (  # noqa: E402
    DB_TIMEOUT_S,
    _connect,
    _db_area_rows,
    _db_place_row,
    _db_search_rows,
    _fetch,
)
from agent.tools.areas import (  # noqa: E402
    _AREA_FIELDS,
    MAX_AREAS_PER_CALL,
    _areas_from_registry,
    find_areas,
    find_areas_with_db,
)
from agent.tools.places import (  # noqa: E402
    _FACT_FIELDS,
    _PLACE_FIELDS,
    DEFAULT_RADIUS_M,
    DEFAULT_SEARCH_LIMIT,
    MAX_FACTS_PER_CALL,
    MAX_LINKS_PER_PLACE,
    MAX_PLACES_PER_SEARCH,
    MAX_RADIUS_M,
    MIN_RADIUS_M,
    get_place_facts,
    get_place_facts_with_db,
    search_places,
    search_places_with_db,
)
from agent.tools.services import (  # noqa: E402
    MAX_DETOUR_MINUTES,
    MAX_ROUTE_POINTS,
    MAX_SERVICES_PER_CALL,
    services_near_route,
)

# The registry: what the agent advertises, one line per tool.
TOOL_REGISTRARS: tuple[Callable[[Any, Callable[[Any, dict], dict]], None], ...] = (
    areas.register_find_areas,
    places.register_search_places,
    places.register_get_place_facts,
    services.register_services_near_route,
)


def register_all(agent: Any, remember: Callable[[Any, dict], dict]) -> None:
    """Advertise every tool in the registry on ``agent``."""
    for register in TOOL_REGISTRARS:
        register(agent, remember)


# Re-exported so `from agent import tools` keeps the historical tool surface.
__all__ = [
    "DB_TIMEOUT_S",
    "DEFAULT_RADIUS_M",
    "DEFAULT_SEARCH_LIMIT",
    "ERR_AREA_REGISTRY",
    "ERR_BAD_ARGUMENT",
    "ERR_DB_UNAVAILABLE",
    "ERR_EMPTY_QUERY",
    "ERR_NOT_FOUND",
    "ERR_NOT_IMPLEMENTED",
    "MAX_AREAS_PER_CALL",
    "MAX_DETOUR_MINUTES",
    "MAX_FACTS_PER_CALL",
    "MAX_LINKS_PER_PLACE",
    "MAX_PLACES_PER_SEARCH",
    "MAX_RADIUS_M",
    "MAX_ROUTE_POINTS",
    "MAX_SERVICES_PER_CALL",
    "MIN_RADIUS_M",
    "TOOL_REGISTRARS",
    "_AREA_FIELDS",
    "_FACT_FIELDS",
    "_PLACE_FIELDS",
    "_areas_from_registry",
    "_clamp",
    "_connect",
    "_db",
    "_db_area_rows",
    "_db_place_row",
    "_db_search_rows",
    "_envelope",
    "_fetch",
    "_norm_term",
    "_positive_int",
    "_project",
    "find_areas",
    "find_areas_with_db",
    "get_place_facts",
    "get_place_facts_with_db",
    "register_all",
    "search_places",
    "search_places_with_db",
    "services_near_route",
]

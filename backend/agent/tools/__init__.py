"""Bounded tool surface for the interpretation agent.

The registry lives here: one line per tool the agent advertises. Adding a tool
means a new module under ``tools/`` plus one entry in :data:`TOOL_REGISTRARS`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
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


def _project(row: Any, fields: tuple[str, ...]) -> dict:
    """Only these fields — from a mapping or from a model, whichever we hold."""
    if isinstance(row, Mapping):
        return {f: row.get(f) for f in fields if f in row}
    return {f: getattr(row, f, None) for f in fields}


def _fetch(repos: Any, fn: Callable[[Any], Any]) -> tuple[Any, str | None, str | None]:
    """Run `fn(repos)`, or say why the database could not be reached.

    Never raises: a tool that cannot read reports it, so the model learns the
    answer is missing instead of the whole run dying on it.
    """
    if repos is None:
        return None, ERR_DB_UNAVAILABLE, "database not available"
    try:
        return fn(repos), None, None
    except ImportError as exc:
        log.warning("tool: db driver unavailable: %s", exc)
        return None, ERR_DB_UNAVAILABLE, "database driver not available"
    except Exception as exc:
        log.warning("tool: db access failed: %s", exc)
        return None, ERR_DB_UNAVAILABLE, "database not reachable"


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _norm_term(text: str) -> str:
    """Lowercase, ё-folded, whitespace-collapsed — for term matching only."""
    return " ".join(str(text).lower().replace("ё", "е").split())


# The registry needs the submodules; they import the helpers above, so this
# import has to sit below them.
from agent.tools import areas, places, services  # noqa: E402

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



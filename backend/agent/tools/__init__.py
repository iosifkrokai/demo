"""Bounded tool surface for the interpretation agent."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

log = logging.getLogger(__name__)

ERR_EMPTY_QUERY = "empty_query"
ERR_BAD_ARGUMENT = "bad_argument"
ERR_NOT_FOUND = "not_found"
ERR_DB_UNAVAILABLE = "db_unavailable"

ToolAnswer = dict[str, Any]
Remember = Callable[[Any, ToolAnswer], ToolAnswer]


def envelope(
    tool: str,
    results: list[dict[str, Any]],
    provenance: dict[str, Any],
    *,
    capped: bool = False,
    error: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """One shape for every tool answer: data + provenance + honest statuses."""
    return {
        "results": results,
        "count": len(results),
        "capped": capped,
        "error": error,
        "message": message,
        "provenance": {"tool": tool, **provenance},
    }


def clamp(
    value: Any, low: int | float, high: int | float, default: int | float
) -> tuple[int | float, bool]:
    """Clamp a number the model sent into [low, high]; report whether we did."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return default, False
    if value < low:
        return low, value != low
    if value > high:
        return high, True
    return value, False


def project(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """Only these fields — from a mapping or from a model, whichever we hold."""
    if isinstance(row, Mapping):
        return {f: row.get(f) for f in fields if f in row}
    return {f: getattr(row, f, None) for f in fields}


def fetch(repos: Any, fn: Callable[[Any], Any]) -> tuple[Any, str | None, str | None]:
    """Run `fn(repos)`, or say why the database could not be reached."""
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


def positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


from agent.tools import areas, places, plan, services, verify  # noqa: E402

TOOL_REGISTRARS: tuple[Callable[[Any, Remember], None], ...] = (
    areas.register_find_areas,
    places.register_find_places,
    plan.register_plan_walk,
    places.register_get_place_facts,
    verify.register_check_requirements,
    services.register_nearby_services,
)


def register_all(agent: Any, remember: Remember) -> None:
    """Advertise every tool in the registry on ``agent``."""
    for register in TOOL_REGISTRARS:
        register(agent, remember)

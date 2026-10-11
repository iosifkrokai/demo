"""``find_areas`` — resolve a territory name to canonical area slugs."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from agent.schema import PlanningDeps
from agent.tools import (
    ERR_BAD_ARGUMENT,
    Remember,
    ToolAnswer,
    envelope,
    fetch,
    project,
)

MAX_AREAS_PER_CALL = 8

_AREA_FIELDS = ("code", "name_ru", "name_en", "kind")


def find_areas(term: str, locale: str = "ru", *, repos: Any = None) -> dict[str, Any]:
    """Resolve a territory name ("старый город", "Новогрудок") to area slugs.

    A name that matches nothing returns an empty result set — never a guessed radius.
    """
    provenance: dict[str, Any] = {
        "source": "areas",
        "locale": locale if locale in ("ru", "en") else "ru",
        "result_cap": MAX_AREAS_PER_CALL,
    }
    if not term.strip():
        return envelope(
            "find_areas",
            [],
            provenance,
            error=ERR_BAD_ARGUMENT,
            message="term must be a non-empty string",
        )

    rows, error, message = fetch(repos, lambda store: store.areas.search(term, MAX_AREAS_PER_CALL))
    if error is not None:
        return envelope("find_areas", [], provenance, error=error, message=message)
    results = [project(row, _AREA_FIELDS) for row in (rows or [])[:MAX_AREAS_PER_CALL]]
    return envelope("find_areas", results, provenance, capped=len(rows or []) > len(results))


def register_find_areas(agent: Any, _remember: Remember) -> None:
    """Advertise ``find_areas`` to the agent.

    The Python name differs from the advertised one on purpose: a nested
    ``find_areas`` would shadow the module function this body has to call.
    """

    @agent.tool(name="find_areas")
    def _find_areas(ctx: RunContext[PlanningDeps], term: str, locale: str = "ru") -> ToolAnswer:
        """Resolve a territory name to canonical area codes for this region."""
        return find_areas(term, locale, repos=ctx.deps.repos)

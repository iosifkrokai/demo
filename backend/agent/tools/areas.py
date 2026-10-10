"""``find_areas`` — resolve a territory name to canonical area slugs."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from pydantic_ai import RunContext

from agent.schema import InterpretDeps
from agent.tools import (
    ERR_AREA_REGISTRY,
    ERR_BAD_ARGUMENT,
    _envelope,
    _fetch,
    _norm_term,
    _project,
)

log = logging.getLogger(__name__)

MAX_AREAS_PER_CALL = 8

_AREA_FIELDS = ("code", "name_ru", "name_en", "kind")


def _areas_from_registry(term: str, locale: str, limit: int) -> list[dict]:
    """Canonical area slugs from the versioned registry (``reference/areas.py``)."""
    from reference.areas import load_areas, resolve_area

    registry = load_areas()
    found: list[str] = []
    slug = resolve_area(term, locale)
    if slug:
        found.append(slug)
    needle = _norm_term(term)
    if needle:
        for candidate, area in registry.items():
            if candidate in found:
                continue
            terms = [candidate, area.get("name_ru") or "", area.get("name_en") or ""]
            aliases = area.get("aliases") or {}
            for loc in ("ru", "en"):
                terms.extend(aliases.get(loc) or [])
            if any(needle in _norm_term(t) for t in terms if t):
                found.append(candidate)
            if len(found) >= limit:
                break
    out: list[dict] = []
    for candidate in found[:limit]:
        area = registry.get(candidate) or {}
        out.append(
            {
                "code": candidate,
                "name_ru": area.get("name_ru"),
                "name_en": area.get("name_en"),
                "kind": area.get("kind"),
            }
        )
    return out


def find_areas(term: str, locale: str = "ru", *, repos: Any = None) -> dict:
    """Resolve a territory name ("старый город", "Новогрудок") to area slugs.

    A name that matches nothing returns an empty result set — never a guessed radius.
    """
    provenance: dict[str, Any] = {
        "source": "areas.json",
        "locale": locale if locale in ("ru", "en") else "ru",
        "result_cap": MAX_AREAS_PER_CALL,
    }
    if not isinstance(term, str) or not term.strip():
        return _envelope(
            "find_areas",
            [],
            provenance,
            error=ERR_BAD_ARGUMENT,
            message="term must be a non-empty string",
        )

    try:
        found = _areas_from_registry(term, provenance["locale"], MAX_AREAS_PER_CALL)
    except ImportError:
        provenance["source"] = "areas (db fallback)"
        rows, error, message = _fetch(
            repos, lambda store: store.areas.search(term, MAX_AREAS_PER_CALL)
        )
        if error is not None:
            return _envelope("find_areas", [], provenance, error=error, message=message)
        results = [_project(row, _AREA_FIELDS) for row in (rows or [])[:MAX_AREAS_PER_CALL]]
        return _envelope("find_areas", results, provenance, capped=len(rows or []) > len(results))
    except Exception as exc:
        log.warning("find_areas: area registry unusable: %s", exc)
        return _envelope(
            "find_areas",
            [],
            provenance,
            error=ERR_AREA_REGISTRY,
            message="area registry is unusable; areas cannot be resolved",
        )

    results = [_project(row, _AREA_FIELDS) for row in found[:MAX_AREAS_PER_CALL]]
    return _envelope("find_areas", results, provenance, capped=len(found) >= MAX_AREAS_PER_CALL)


def register_find_areas(agent: Any, remember: Callable[[Any, dict], dict]) -> None:
    """Advertise ``find_areas`` to the agent.

    The Python name differs from the advertised one on purpose: a nested
    ``find_areas`` would shadow the module function this body has to call.
    """

    @agent.tool(name="find_areas")
    def _find_areas(ctx: RunContext[InterpretDeps], term: str, locale: str = "ru") -> dict:
        """Resolve a territory name to canonical area codes for this region."""
        return find_areas(term, locale, repos=ctx.deps.repos)

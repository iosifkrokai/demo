"""``search_places`` and ``get_place_facts`` — the place lookups."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.model import RunContext
from agent.schema import InterpretDeps
from agent.tools import (
    ERR_BAD_ARGUMENT,
    ERR_EMPTY_QUERY,
    ERR_NOT_FOUND,
    _clamp,
    _db,
    _envelope,
    _positive_int,
    _project,
)
from reference.taxonomy import db_values

MAX_PLACES_PER_SEARCH = 8
MAX_FACTS_PER_CALL = 1
MAX_LINKS_PER_PLACE = 3
MIN_RADIUS_M = 100.0
MAX_RADIUS_M = 20_000.0
DEFAULT_SEARCH_LIMIT = 5
DEFAULT_RADIUS_M = 1_000.0

_PLACE_FIELDS = ("id", "name", "category", "town", "district", "lat", "lon")
_FACT_FIELDS = (
    "name",
    "category",
    "town",
    "district",
    "opening_hours",
    "ticket_price",
    "visit_minutes",
    "source_url",
)


def search_places(
    query: str,
    category_codes: list[str] | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    near_lat: float | None = None,
    near_lon: float | None = None,
    radius_m: float | None = None,
) -> dict:
    """The ids are the only handles the agent may put into a requirement; unknown
    category codes are dropped, never sent to SQL.
    """
    return search_places_with_db(None, query, category_codes, limit, near_lat, near_lon, radius_m)


def search_places_with_db(
    db: Any,
    query: str,
    category_codes: list[str] | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    near_lat: float | None = None,
    near_lon: float | None = None,
    radius_m: float | None = None,
) -> dict:
    """`search_places` reusing a caller-owned connection (used by the agent)."""
    requested = [c for c in (category_codes or []) if isinstance(c, str) and c.strip()]
    db_cats = db_values(requested)
    dropped = [c for c in requested if c not in db_cats]

    want, limit_clamped = _clamp(limit, 1, MAX_PLACES_PER_SEARCH, DEFAULT_SEARCH_LIMIT)
    want = int(want)

    near: tuple[float, float, float] | None = None
    radius_clamped = False
    if isinstance(near_lat, (int, float)) and isinstance(near_lon, (int, float)):
        radius, radius_clamped = _clamp(radius_m, MIN_RADIUS_M, MAX_RADIUS_M, DEFAULT_RADIUS_M)
        near = (float(near_lat), float(near_lon), float(radius))

    provenance: dict[str, Any] = {
        "source": "places",
        "strategy": "nearby" if near else "keyword",
        "categories": db_cats,
        "dropped_codes": dropped,
        "result_cap": MAX_PLACES_PER_SEARCH,
        "radius_m": near[2] if near else None,
    }

    if not isinstance(query, str) or not query.strip():
        return _envelope(
            "search_places",
            [],
            provenance,
            error=ERR_EMPTY_QUERY,
            message="query must be a non-empty string",
        )

    def run(conn: Any) -> list[dict]:
        fetch = MAX_PLACES_PER_SEARCH if db_cats else want
        rows = _db._db_search_rows(conn, query=query.strip(), limit=fetch, near=near)
        if db_cats:
            rows = [r for r in rows if r.get("category") in db_cats]
        rows = [_project(r, _PLACE_FIELDS) for r in rows]
        return rows[:want]

    rows, error, message = _db._fetch(db, run)
    if error is not None:
        return _envelope("search_places", [], provenance, error=error, message=message)
    results = rows or []
    return _envelope(
        "search_places",
        results,
        provenance,
        capped=limit_clamped or radius_clamped,
    )


def get_place_facts(place_id: int) -> dict:
    """The provenance says so (`fact_status="raw_unverified"`); a missing place
    returns ``error="not_found"``.
    """
    return get_place_facts_with_db(None, place_id)


def get_place_facts_with_db(db: Any, place_id: int) -> dict:
    """`get_place_facts` reusing a caller-owned connection (used by the agent)."""
    provenance: dict[str, Any] = {
        "source": "places",
        "result_cap": MAX_FACTS_PER_CALL,
        "fact_status": "raw_unverified",
    }
    if not _positive_int(place_id):
        return _envelope(
            "get_place_facts",
            [],
            provenance,
            error=ERR_BAD_ARGUMENT,
            message="place_id must be a positive integer",
        )

    def run(conn: Any) -> list[dict]:
        row = _db._db_place_row(conn, place_id)
        if row is None:
            return []
        facts = _project(row, _FACT_FIELDS)
        links = row.get("links")
        facts["links"] = (
            list(links)[:MAX_LINKS_PER_PLACE] if isinstance(links, (list, tuple)) else None
        )
        return [facts]

    rows, error, message = _db._fetch(db, run)
    if error is not None:
        return _envelope("get_place_facts", [], provenance, error=error, message=message)
    if not rows:
        return _envelope(
            "get_place_facts",
            [],
            provenance,
            error=ERR_NOT_FOUND,
            message=f"no place with id {place_id}",
        )
    return _envelope("get_place_facts", rows[:MAX_FACTS_PER_CALL], provenance)


def register_search_places(agent: Any, remember: Callable[[Any, dict], dict]) -> None:
    """Advertise ``search_places`` to the agent."""

    @agent.tool
    def search_places(
        ctx: RunContext[InterpretDeps],
        query: str,
        category_codes: list[str] | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        near_lat: float | None = None,
        near_lon: float | None = None,
        radius_m: float | None = None,
    ) -> dict:
        """Search real places by text and/or canonical category codes (capped)."""
        return remember(
            ctx,
            search_places_with_db(
                ctx.deps.db, query, category_codes, limit, near_lat, near_lon, radius_m
            ),
        )


def register_get_place_facts(agent: Any, remember: Callable[[Any, dict], dict]) -> None:
    """Advertise ``get_place_facts`` to the agent."""

    @agent.tool
    def get_place_facts(ctx: RunContext[InterpretDeps], place_id: int) -> dict:
        """Raw stored facts (hours, price, town, source URL) about one place."""
        return remember(ctx, get_place_facts_with_db(ctx.deps.db, place_id))

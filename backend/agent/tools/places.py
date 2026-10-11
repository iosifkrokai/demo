"""``search_places`` and ``get_place_facts`` — the place lookups.

Both go through the place repository, never a connection.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from agent.schema import InterpretDeps
from agent.tools import (
    ERR_BAD_ARGUMENT,
    ERR_EMPTY_QUERY,
    ERR_NOT_FOUND,
    Remember,
    clamp,
    envelope,
    fetch,
    positive_int,
    project,
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
    *,
    repos: Any = None,
) -> dict[str, Any]:
    """The ids are the only handles the agent may put into a requirement; unknown
    category codes are dropped, never sent to SQL.
    """
    requested = [c for c in (category_codes or []) if c.strip()]
    db_cats = db_values(requested)
    dropped = [c for c in requested if c not in db_cats]

    want, limit_clamped = clamp(limit, 1, MAX_PLACES_PER_SEARCH, DEFAULT_SEARCH_LIMIT)
    want = int(want)

    near: tuple[float, float, float] | None = None
    radius_clamped = False
    if isinstance(near_lat, (int, float)) and isinstance(near_lon, (int, float)):
        radius, radius_clamped = clamp(radius_m, MIN_RADIUS_M, MAX_RADIUS_M, DEFAULT_RADIUS_M)
        near = (float(near_lat), float(near_lon), float(radius))

    provenance: dict[str, Any] = {
        "source": "places",
        "strategy": "nearby" if near else "keyword",
        "categories": db_cats,
        "dropped_codes": dropped,
        "result_cap": MAX_PLACES_PER_SEARCH,
        "radius_m": near[2] if near else None,
    }

    if not query.strip():
        return envelope(
            "search_places",
            [],
            provenance,
            error=ERR_EMPTY_QUERY,
            message="query must be a non-empty string",
        )

    def run(store: Any) -> list[dict[str, Any]]:
        cap = MAX_PLACES_PER_SEARCH if db_cats else want
        if near is not None:
            found = store.places.nearby(near[0], near[1], radius_km=near[2] / 1000.0, limit=cap)
        else:
            found = store.places.keyword_search(query.strip(), limit=cap)
        if db_cats:
            found = [place for place in found if place.category in db_cats]
        return [project(place, _PLACE_FIELDS) for place in found][:want]

    rows, error, message = fetch(repos, run)
    if error is not None:
        return envelope("search_places", [], provenance, error=error, message=message)
    results = rows or []
    return envelope(
        "search_places",
        results,
        provenance,
        capped=limit_clamped or radius_clamped,
    )


def get_place_facts(place_id: int, *, repos: Any = None) -> dict[str, Any]:
    """The provenance says so (`fact_status="raw_unverified"`); a missing place
    returns ``error="not_found"``.
    """
    provenance: dict[str, Any] = {
        "source": "places",
        "result_cap": MAX_FACTS_PER_CALL,
        "fact_status": "raw_unverified",
    }
    if not positive_int(place_id):
        return envelope(
            "get_place_facts",
            [],
            provenance,
            error=ERR_BAD_ARGUMENT,
            message="place_id must be a positive integer",
        )

    def run(store: Any) -> list[dict[str, Any]]:
        place = store.places.get_by_id(place_id)
        if place is None:
            return []
        facts = project(place, _FACT_FIELDS)
        links = place.links
        facts["links"] = (
            list(links)[:MAX_LINKS_PER_PLACE] if isinstance(links, (list, tuple)) else None
        )
        return [facts]

    rows, error, message = fetch(repos, run)
    if error is not None:
        return envelope("get_place_facts", [], provenance, error=error, message=message)
    if not rows:
        return envelope(
            "get_place_facts",
            [],
            provenance,
            error=ERR_NOT_FOUND,
            message=f"no place with id {place_id}",
        )
    return envelope("get_place_facts", rows[:MAX_FACTS_PER_CALL], provenance)


def register_search_places(agent: Any, remember: Remember) -> None:
    """Advertise ``search_places`` to the agent.

    The Python name differs from the advertised one on purpose: a nested
    ``search_places`` would shadow the module function this body has to call.
    """

    @agent.tool(name="search_places")
    def _search_places(
        ctx: RunContext[InterpretDeps],
        query: str,
        category_codes: list[str] | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        near_lat: float | None = None,
        near_lon: float | None = None,
        radius_m: float | None = None,
    ) -> dict[str, Any]:
        """Search real places by text and/or canonical category codes (capped)."""
        return remember(
            ctx,
            search_places(
                query,
                category_codes,
                limit,
                near_lat,
                near_lon,
                radius_m,
                repos=ctx.deps.repos,
            ),
        )


def register_get_place_facts(agent: Any, remember: Remember) -> None:
    """Advertise ``get_place_facts`` to the agent.

    Named apart from the module function for the same reason as above.
    """

    @agent.tool(name="get_place_facts")
    def _get_place_facts(ctx: RunContext[InterpretDeps], place_id: int) -> dict[str, Any]:
        """Raw stored facts (hours, price, town, source URL) about one place."""
        return remember(ctx, get_place_facts(place_id, repos=ctx.deps.repos))

"""``find_places`` and ``get_place_facts`` — how the agent sees the dataset.

`find_places` is the only way a place enters the conversation. It fuses three
signals — meaning (local embeddings), category, and proximity — and returns a
short card per place, because the agent's context is a budget and a card is
cheaper than a record.

There are deliberately *two* tools rather than one: folding opening hours,
ticket prices and links into every search result would multiply the tokens for
facts that are only wanted about the two or three places worth keeping.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from agent.schema import PlanningDeps
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
from core import constants
from embeddings import model as embeddings_model
from embeddings.query import embed_query
from routing.select import rrf_fuse

MAX_PLACES_PER_FIND = 8
MAX_FACTS_PER_CALL = 1
MAX_LINKS_PER_PLACE = 3
MAX_BLURB_CHARS = 240
MIN_RADIUS_M = 100.0
MAX_RADIUS_M = 20_000.0
DEFAULT_FIND_LIMIT = 5
DEFAULT_RADIUS_M = 1_000.0

_CARD_FIELDS = (
    "id",
    "name",
    "category",
    "town",
    "district",
    "lat",
    "lon",
    "visit_minutes",
    "opening_hours",
)
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


def _card(place: Any) -> dict[str, Any]:
    card = project(place, _CARD_FIELDS)
    blurb = (getattr(place, "blurb", None) or "").strip()
    card["blurb"] = blurb[:MAX_BLURB_CHARS] or None
    return card


def find_places(
    query: str,
    category_codes: list[str] | None = None,
    limit: int = DEFAULT_FIND_LIMIT,
    near_lat: float | None = None,
    near_lon: float | None = None,
    radius_m: float | None = None,
    *,
    repos: Any = None,
) -> dict[str, Any]:
    """Rank real places against a description, a category, and/or a point.

    The ids are the only handles the agent may put into a plan; an unknown
    category code is dropped, never sent to SQL.
    """
    requested = [c for c in (category_codes or []) if c.strip()]

    want, limit_clamped = clamp(limit, 1, MAX_PLACES_PER_FIND, DEFAULT_FIND_LIMIT)
    want = int(want)

    near: tuple[float, float, float] | None = None
    radius_clamped = False
    if isinstance(near_lat, (int, float)) and isinstance(near_lon, (int, float)):
        radius, radius_clamped = clamp(radius_m, MIN_RADIUS_M, MAX_RADIUS_M, DEFAULT_RADIUS_M)
        near = (float(near_lat), float(near_lon), float(radius))

    provenance: dict[str, Any] = {
        "source": "places",
        "signals": [],
        "categories": [],
        "dropped_codes": requested,
        "result_cap": MAX_PLACES_PER_FIND,
        "radius_m": near[2] if near else None,
    }

    if not query.strip() and not requested and near is None:
        return envelope(
            "find_places",
            [],
            provenance,
            error=ERR_EMPTY_QUERY,
            message="give a description, a category, or a point to search around",
        )

    def run(store: Any) -> list[dict[str, Any]]:
        # Unknown category codes are dropped, never sent to SQL.
        db_cats = store.taxonomy.db_values(requested)
        provenance["categories"] = db_cats
        provenance["dropped_codes"] = [c for c in requested if c not in db_cats]

        pool = constants.RETRIEVAL_POOL_SIZE
        rankings: list[list[tuple[int, float]]] = []

        qvec = embed_query(query) if query.strip() and embeddings_model.is_available() else []
        if qvec:
            rankings.append(
                [(place.id, score) for place, score in store.places.by_embedding(qvec, limit=pool)]
            )
            provenance["signals"].append("embedding")
        if db_cats:
            rankings.append([(p.id, 0.0) for p in store.places.by_category(db_cats, pool)])
            provenance["signals"].append("category")
        if near is not None:
            found = store.places.nearby(near[0], near[1], radius_km=near[2] / 1000.0, limit=pool)
            rankings.append([(p.id, 0.0) for p in found])
            provenance["signals"].append("nearby")

        if not rankings:
            return []
        fused = rrf_fuse(rankings, k=constants.RRF_K)
        top = [pid for pid, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:want]]
        rows = store.places.get_by_ids(top)
        order = {pid: rank for rank, pid in enumerate(top)}
        rows.sort(key=lambda row: order.get(row.id or 0, len(order)))
        return [_card(place) for place in rows]

    rows, error, message = fetch(repos, run)
    if error is not None:
        return envelope("find_places", [], provenance, error=error, message=message)
    results = rows or []
    return envelope(
        "find_places",
        results,
        provenance,
        capped=bool(limit_clamped or radius_clamped or len(results) >= want),
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


def register_find_places(agent: Any, remember: Remember) -> None:
    """Advertise ``find_places`` to the agent.

    The Python name differs from the advertised one on purpose: a nested
    ``find_places`` would shadow the module function this body has to call.
    """

    @agent.tool(name="find_places")
    def _find_places(
        ctx: RunContext[PlanningDeps],
        query: str,
        category_codes: list[str] | None = None,
        limit: int = DEFAULT_FIND_LIMIT,
        near_lat: float | None = None,
        near_lon: float | None = None,
        radius_m: float | None = None,
    ) -> dict[str, Any]:
        """Find real places matching a description, a category, and/or a point.

        Returns a short card per place — id, name, category, town, location and
        how long a visit is estimated to take. The id is the only handle you may
        use to refer to a place afterwards.
        """
        return remember(
            ctx,
            find_places(
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
    def _get_place_facts(ctx: RunContext[PlanningDeps], place_id: int) -> dict[str, Any]:
        """Raw stored facts (hours, price, town, source URL) about one place."""
        return remember(ctx, get_place_facts(place_id, repos=ctx.deps.repos))

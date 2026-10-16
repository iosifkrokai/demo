"""``find_places`` and ``get_place_facts`` — how the agent sees the dataset."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic_ai import RunContext

from agent.schema import PlanningDeps
from agent.tools import (
    ERR_BAD_ARGUMENT,
    ERR_EMPTY_QUERY,
    ERR_NOT_FOUND,
    clamp,
    envelope,
    fetch,
    positive_int,
    project,
)
from core import constants
from db.embeddings import model as embeddings_model
from db.embeddings.query import embed_query
from routing.select import rrf_fuse

SIGNALS = ("embedding", "alias", "category", "nearby")

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
    "is_free",
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


class Where(BaseModel):
    """Where to look: a named territory, a point with a radius, or both."""

    area: str | None = None
    near_lat: float | None = None
    near_lon: float | None = None
    radius_m: float | None = None


def _in_box(place: Any, box: tuple[float, float, float, float] | None) -> bool:
    return box is None or _inside(place.lat, place.lon, box)


def _text_hits(store: Any, query: str, box: Any, pool: int) -> list[tuple[int, float]] | None:
    """Vector hits — only when the query says something and a model can embed it."""
    if not query.strip() or not embeddings_model.is_available():
        return None
    qvec = embed_query(query)
    if not qvec:
        return None
    found = store.places.by_embedding(qvec, limit=pool, region_bbox=box)
    return [(place.id, score) for place, score in found if _in_box(place, box)]


def _alias_hits(store: Any, query: str, box: Any, pool: int) -> list[tuple[int, float]] | None:
    """The other names a place answers to, matched as trigrams — no model needed."""
    if not query.strip():
        return None
    found = store.places.by_alias(query, limit=pool, region_bbox=box)
    return [(place.id, score) for place, score in found if _in_box(place, box)]


def _category_hits(
    store: Any, codes: list[str], box: Any, pool: int
) -> list[tuple[int, float]] | None:
    """Everything the catalogue files under the codes the request named."""
    if not codes:
        return None
    return [(p.id, 0.0) for p in store.places.by_category(codes, pool) if _in_box(p, box)]


def _near_hits(
    store: Any, near: tuple[float, float, float] | None, box: Any, pool: int
) -> list[tuple[int, float]] | None:
    """Everything inside the radius of the point the request named."""
    if near is None:
        return None
    around = store.places.nearby(near[0], near[1], radius_km=near[2] / 1000.0, limit=pool)
    return [(p.id, 0.0) for p in around if _in_box(p, box)]


def card_of(place: Any) -> dict[str, Any]:
    """One place as the agent and the client both read it."""
    card = project(place, _CARD_FIELDS)
    blurb = (getattr(place, "blurb", None) or "").strip()
    card["blurb"] = blurb[:MAX_BLURB_CHARS] or None
    return card


def catalogue_rows(
    place_ids: list[int],
    *,
    categories: list[str],
    area: str | None,
    repos: Any,
    limit: int = constants.MAX_CATALOGUE_PLACES,
) -> tuple[list[Any], bool]:
    """The places of a catalogue: every one the named codes cover in the territory —
    the request itself, not the agent's reading of it. A request with no code to
    retrieve by leaves the list to the places the agent named. The flag says the list
    was cut, at `limit` or at the pool a code was read from.
    """
    if categories:
        rows, error, _message = fetch(
            repos, lambda store: _catalogue_by_code(store, categories, area, limit)
        )
        if error is None and rows:
            return rows

    chosen, error, _message = fetch(repos, lambda store: store.places.get_by_ids(place_ids))
    found = chosen if error is None and chosen else []
    return found, len(found) >= limit


def _catalogue_by_code(
    store: Any, categories: list[str], area: str | None, limit: int
) -> tuple[list[Any], bool]:
    """Each code ranked on its own, so no one code can crowd another out of the list."""
    codes = store.taxonomy.db_values(categories)
    box = store.areas.bbox(area) if area else None
    rankings = [
        hits
        for code in codes
        if (hits := _category_hits(store, [code], box, constants.RETRIEVAL_POOL_SIZE))
    ]
    if not rankings:
        return [], False
    fused = rrf_fuse(rankings, k=constants.RRF_K)
    top = [pid for pid, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:limit]]
    rows = store.places.get_by_ids(top)
    order = {pid: rank for rank, pid in enumerate(top)}
    rows.sort(key=lambda row: order.get(row.id or 0, len(order)))
    truncated = any(len(hits) >= constants.RETRIEVAL_POOL_SIZE for hits in rankings)
    return rows, truncated or len(rows) >= limit


def _inside(lat: float, lon: float, box: tuple[float, float, float, float]) -> bool:
    """Is a point inside a (south, west, north, east) box?"""
    south, west, north, east = box
    return south <= lat <= north and west <= lon <= east


def find_places(
    query: str,
    category_codes: list[str] | None = None,
    limit: int = DEFAULT_FIND_LIMIT,
    *,
    where: Where | None = None,
    repos: Any = None,
    region_bbox: list[float] | None = None,
) -> dict[str, Any]:
    """Rank real places against a description, a category, and/or a point."""
    where = where or Where()
    requested = [c for c in (category_codes or []) if c.strip()]

    want, limit_clamped = clamp(limit, 1, constants.MAX_PLACES_PER_FIND, DEFAULT_FIND_LIMIT)
    want = int(want)

    near: tuple[float, float, float] | None = None
    radius_clamped = False
    if isinstance(where.near_lat, (int, float)) and isinstance(where.near_lon, (int, float)):
        radius, radius_clamped = clamp(where.radius_m, MIN_RADIUS_M, MAX_RADIUS_M, DEFAULT_RADIUS_M)
        near = (float(where.near_lat), float(where.near_lon), float(radius))

    requested_box: tuple[float, float, float, float] | None = None
    if region_bbox and len(region_bbox) == constants.BBOX_COORDS:
        requested_box = (
            float(region_bbox[0]),
            float(region_bbox[1]),
            float(region_bbox[2]),
            float(region_bbox[3]),
        )

    provenance: dict[str, Any] = {
        "source": "places",
        "signals": [],
        "categories": [],
        "dropped_codes": requested,
        "result_cap": constants.MAX_PLACES_PER_FIND,
        "radius_m": near[2] if near else None,
        "area": where.area,
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
        db_cats = store.taxonomy.db_values(requested)
        provenance["categories"] = db_cats
        provenance["dropped_codes"] = [c for c in requested if c not in db_cats]

        box = requested_box
        if where.area:
            resolved = store.areas.bbox(where.area)
            if resolved is not None:
                box = resolved
            else:
                provenance["area_filter"] = "no_boundary"
        provenance["region_bbox"] = list(box) if box is not None else None

        pool = constants.RETRIEVAL_POOL_SIZE
        hits = (
            _text_hits(store, query, box, pool),
            _alias_hits(store, query, box, pool),
            _category_hits(store, db_cats, box, pool),
            _near_hits(store, near, box, pool),
        )
        provenance["signals"] = [name for name, hit in zip(SIGNALS, hits, strict=False) if hit]

        rankings = [hit for hit in hits if hit]
        if not rankings:
            return []
        fused = rrf_fuse(rankings, k=constants.RRF_K)
        top = [pid for pid, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:want]]
        rows = store.places.get_by_ids(top)
        order = {pid: rank for rank, pid in enumerate(top)}
        rows.sort(key=lambda row: order.get(row.id or 0, len(order)))
        return [card_of(place) for place in rows]

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


def register_find_places(agent: Any) -> None:
    """Advertise ``find_places`` to the agent."""

    @agent.tool(name="find_places")
    def _find_places(
        ctx: RunContext[PlanningDeps],
        query: str,
        *,
        category_codes: list[str] | None = None,
        limit: int = DEFAULT_FIND_LIMIT,
        where: Where | None = None,
    ) -> dict[str, Any]:
        """Find real places matching a description, a category, and/or a point."""
        return find_places(
            query,
            category_codes,
            limit,
            where=where,
            repos=ctx.deps.repos,
            region_bbox=list(ctx.deps.region_bbox) if ctx.deps.region_bbox else None,
        )


def register_get_place_facts(agent: Any) -> None:
    """Advertise ``get_place_facts`` to the agent."""

    @agent.tool(name="get_place_facts")
    def _get_place_facts(ctx: RunContext[PlanningDeps], place_id: int) -> dict[str, Any]:
        """Raw stored facts (hours, price, town, source URL) about one place."""
        return get_place_facts(place_id, repos=ctx.deps.repos)

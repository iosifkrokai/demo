"""``find_places`` and ``get_place_facts`` — how the agent sees the dataset."""

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
from db.embeddings import model as embeddings_model
from db.embeddings.query import embed_query
from routing.select import rrf_fuse

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


def _inside(lat: float, lon: float, box: tuple[float, float, float, float]) -> bool:
    """Is a point inside a (south, west, north, east) box?"""
    south, west, north, east = box
    return south <= lat <= north and west <= lon <= east


def find_places(
    query: str,
    category_codes: list[str] | None = None,
    limit: int = DEFAULT_FIND_LIMIT,
    *,
    near_lat: float | None = None,
    near_lon: float | None = None,
    radius_m: float | None = None,
    area: str | None = None,
    repos: Any = None,
    region_bbox: list[float] | None = None,
) -> dict[str, Any]:
    """Rank real places against a description, a category, and/or a point."""
    requested = [c for c in (category_codes or []) if c.strip()]

    want, limit_clamped = clamp(limit, 1, constants.MAX_PLACES_PER_FIND, DEFAULT_FIND_LIMIT)
    want = int(want)

    near: tuple[float, float, float] | None = None
    radius_clamped = False
    if isinstance(near_lat, (int, float)) and isinstance(near_lon, (int, float)):
        radius, radius_clamped = clamp(radius_m, MIN_RADIUS_M, MAX_RADIUS_M, DEFAULT_RADIUS_M)
        near = (float(near_lat), float(near_lon), float(radius))

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
        "area": area,
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
        if area:
            resolved = store.areas.bbox(area)
            if resolved is not None:
                box = resolved
            else:
                provenance["area_filter"] = "no_boundary"
        provenance["region_bbox"] = list(box) if box is not None else None

        def keep(place: Any) -> bool:
            return box is None or _inside(place.lat, place.lon, box)

        pool = constants.RETRIEVAL_POOL_SIZE
        rankings: list[list[tuple[int, float]]] = []

        qvec = embed_query(query) if query.strip() and embeddings_model.is_available() else []
        if qvec:
            found = store.places.by_embedding(qvec, limit=pool, region_bbox=box)
            rankings.append([(place.id, score) for place, score in found if keep(place)])
            provenance["signals"].append("embedding")
        if db_cats:
            by_cat = store.places.by_category(db_cats, pool)
            rankings.append([(p.id, 0.0) for p in by_cat if keep(p)])
            provenance["signals"].append("category")
        if near is not None:
            around = store.places.nearby(near[0], near[1], radius_km=near[2] / 1000.0, limit=pool)
            rankings.append([(p.id, 0.0) for p in around if keep(p)])
            provenance["signals"].append("nearby")

        rankings = [ranking for ranking in rankings if ranking]
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
    """Advertise ``find_places`` to the agent."""

    @agent.tool(name="find_places")
    def _find_places(
        ctx: RunContext[PlanningDeps],
        query: str,
        *,
        category_codes: list[str] | None = None,
        limit: int = DEFAULT_FIND_LIMIT,
        near_lat: float | None = None,
        near_lon: float | None = None,
        radius_m: float | None = None,
        area: str | None = None,
    ) -> dict[str, Any]:
        """Find real places matching a description, a category, and/or a point."""
        return remember(
            ctx,
            find_places(
                query,
                category_codes,
                limit,
                near_lat=near_lat,
                near_lon=near_lon,
                radius_m=radius_m,
                area=area,
                repos=ctx.deps.repos,
                region_bbox=list(ctx.deps.region_bbox) if ctx.deps.region_bbox else None,
            ),
        )


def register_get_place_facts(agent: Any, remember: Remember) -> None:
    """Advertise ``get_place_facts`` to the agent."""

    @agent.tool(name="get_place_facts")
    def _get_place_facts(ctx: RunContext[PlanningDeps], place_id: int) -> dict[str, Any]:
        """Raw stored facts (hours, price, town, source URL) about one place."""
        return remember(ctx, get_place_facts(place_id, repos=ctx.deps.repos))

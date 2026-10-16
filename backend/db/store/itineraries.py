"""The curated walks: the `itineraries` tables, and the read of `places` that
fills each stop in.
"""

from __future__ import annotations

import logging
from typing import Any

from db.models.itinerary import Itinerary
from db.store.base import PostgresRepository
from db.store.columns import ITINERARY_SELECT
from db.store.mappers import itinerary_from_row, place_payload
from db.store.places import PostgresPlaceRepository
from db.store.taxonomy import PostgresCategoryRepository

log = logging.getLogger(__name__)


class PostgresItineraryRepository(PostgresRepository):
    """The authored walks, in the order the catalogue shows them."""

    def authored(self) -> list[Itinerary]:
        """Every curated walk with its stop keys attached."""
        with self._cursor() as cur:
            cur.execute(f"SELECT {ITINERARY_SELECT} FROM itineraries ORDER BY position, id")
            walks = [itinerary_from_row(row) for row in cur.fetchall()]
            cur.execute(
                "SELECT itinerary_id, source_url FROM itinerary_stops "
                "ORDER BY itinerary_id, ordinal"
            )
            by_id: dict[str, list[str]] = {}
            for row in cur.fetchall():
                by_id.setdefault(row["itinerary_id"], []).append(row["source_url"])
        return [
            Itinerary(
                id=walk.id,
                title=walk.title,
                blurb=walk.blurb,
                transport=walk.transport,
                position=walk.position,
                stops=tuple(by_id.get(walk.id, ())),
            )
            for walk in walks
        ]


def _split_by_role(
    payloads: list[dict[str, Any]],
    taxonomy: PostgresCategoryRepository,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition resolved places into (stops, services) using the taxonomy."""
    stops: list[dict[str, Any]] = []
    services: list[dict[str, Any]] = []
    for payload in payloads:
        try:
            is_service = taxonomy.role(payload["category"]) == "service"
        except Exception:
            is_service = False
        (services if is_service else stops).append(payload)
    return stops, services


def resolve_itineraries(
    places: PostgresPlaceRepository,
    taxonomy: PostgresCategoryRepository,
    items: list[Itinerary],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Attach dataset facts to every stop, in the authored order."""
    wanted = [key for item in items for key in item.stops]
    found, missing = places.get_by_source_urls(wanted)
    if missing:
        log.warning("itineraries: %d stop(s) no longer resolve: %s", len(missing), missing)

    out: list[dict[str, Any]] = []
    for item in items:
        payloads = [place_payload(found[key]) for key in item.stops if key in found]
        stops, services = _split_by_role(payloads, taxonomy)
        visit_minutes = sum(s["visit_minutes"] or 0 for s in stops)
        out.append(
            {
                "id": item.id,
                "title": item.title,
                "blurb": item.blurb,
                "transport": item.transport,
                "stop_count": len(stops),
                "visit_minutes": visit_minutes,
                "stops": stops,
                "services": services,
            }
        )
    return out, missing

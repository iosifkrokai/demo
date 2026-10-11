"""Ready-made routes: attaching the stored facts to each curated stop.

The file itself is a reference dataset and is read by `reference.itineraries`;
what happens here is the read of `places` that fills its stops in, which is what
a repository is for.
"""

from __future__ import annotations

import logging
from typing import Any

from db.store.mappers import place_payload
from db.store.places import PostgresPlaceRepository
from reference.itineraries import load_itineraries

log = logging.getLogger(__name__)


def _split_by_role(payloads: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Partition resolved places into (stops, services) using the taxonomy.

    An unknown code counts as a stop, never as a service.
    """
    from reference import taxonomy

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
    places: PostgresPlaceRepository, items: list[dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Attach dataset facts to every stop, in the authored order.

    Returns the payloads and keys that did not resolve; services are not stops.
    """
    authored = items if items is not None else load_itineraries()
    wanted = [key for item in authored for key in item.get("stops", [])]
    found, missing = places.get_by_source_urls(wanted)
    if missing:
        log.warning("itineraries: %d stop(s) no longer resolve: %s", len(missing), missing)

    out: list[dict[str, Any]] = []
    for item in authored:
        payloads = [place_payload(found[key]) for key in item.get("stops", []) if key in found]
        stops, services = _split_by_role(payloads)
        visit_minutes = sum(s["visit_minutes"] or 0 for s in stops)
        out.append(
            {
                "id": item["id"],
                "title": item["title"],
                "blurb": item["blurb"],
                "transport": item.get("transport") or "pedestrian",
                "stop_count": len(stops),
                "visit_minutes": visit_minutes,
                "stops": stops,
                "services": services,
            }
        )
    return out, missing

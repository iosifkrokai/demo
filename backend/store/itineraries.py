"""Ready-made routes: a curated itinerary a tourist can open without asking."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.paths import ITINERARIES_JSON

log = logging.getLogger(__name__)

ITINERARIES_PATH = ITINERARIES_JSON

_STOP_SQL = """
    SELECT id, source_url, name, category, town, district, lat, lon,
           visit_minutes, opening_hours, blurb, fun_fact, fun_facts, links,
           ticket_price, photo_url, photo_author, photo_license, photo_source
    FROM places
    WHERE source_url = ANY(%s)
"""


class ItinerariesUnavailable(RuntimeError):
    """The curated file is unreadable — a deployment bug, not a request error."""


def load_itineraries(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the curated file.

    A broken file is a deployment bug, raised loudly, never swallowed silently.
    """
    target = path or ITINERARIES_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ItinerariesUnavailable(f"{target}: {exc}") from exc
    items = raw.get("itineraries")
    if not isinstance(items, list) or not items:
        raise ItinerariesUnavailable(f"{target}: no itineraries")
    return items


def _resolve_stops(conn: Any, keys: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Fetch every stop in one query. Returns (rows by source_url, missing keys)."""
    if not keys:
        return {}, []
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_STOP_SQL, (keys,))
        rows = {row["source_url"]: dict(row) for row in cur.fetchall()}
    missing = [key for key in keys if key not in rows]
    return rows, missing


def _stop_payload(row: dict) -> dict:
    """One stop, in the shape the panel prints. Numbers stay numbers.

    The payload is the catalogue's own (`places.place_payload`).
    """
    from store.places import place_payload

    return place_payload(row)


def _split_by_role(payloads: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Partition resolved places into (stops, services) using the taxonomy.

    An unknown code counts as a stop, never as a service.
    """
    from domain import taxonomy

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
    conn: Any, items: list[dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Attach dataset facts to every stop, in the authored order.

    Returns the payloads and keys that did not resolve; services are not stops.
    """
    authored = items if items is not None else load_itineraries()
    wanted = [key for item in authored for key in item.get("stops", [])]
    rows, missing = _resolve_stops(conn, wanted)
    if missing:
        log.warning("itineraries: %d stop(s) no longer resolve: %s", len(missing), missing)

    out: list[dict[str, Any]] = []
    for item in authored:
        payloads = [_stop_payload(rows[key]) for key in item.get("stops", []) if key in rows]
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

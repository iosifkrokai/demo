"""Ready-made routes: a curated itinerary a tourist can open without asking.

Design rules, in order of importance:

1. **Stops are keys, not copies.** An itinerary lists `places.source_url` values
   — the same provenance key the ingestion scripts write — so a re-seed cannot
   silently repoint a stop at a different place, and nothing about a place
   (name, category, coordinates, opening hours, curated visit time) is duplicated
   into this file. Everything a card shows is read from the database.
2. **A missing key is reported, not hidden.** If a stop no longer resolves the
   itinerary is still served without it, and the key is listed in `missing`;
   silently dropping it would make the route look complete when it is not.
3. **The file is data, not code.** Hand-authored titles and blurbs live in
   `data/itineraries.json`; this module only reads, resolves and totals.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Curated itineraries live next to the other hand-written dataset files.
ITINERARIES_PATH = Path(__file__).resolve().parent.parent / "data" / "itineraries.json"

#: Columns a stop carries into the API. A card prints them, so they are chosen
#: explicitly instead of `SELECT *`.
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

    A broken file is a bug in the deployment; it is raised loudly at the call
    site (the endpoint answers 503 with a reason code) rather than swallowed
    into an empty list, which would look like «у нас нет готовых маршрутов».
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
    from psycopg.rows import dict_row  # local: keeps the import surface small

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_STOP_SQL, (keys,))
        rows = {row["source_url"]: dict(row) for row in cur.fetchall()}
    missing = [key for key in keys if key not in rows]
    return rows, missing


def _stop_payload(row: dict) -> dict:
    """One stop, in the shape the panel prints. Numbers stay numbers.

    A stop is a place like any other, so the payload is the catalogue's own
    (`places.place_payload`): a card and a planned route must not disagree about
    the same point.
    """
    from store.places import place_payload

    return place_payload(row)


def _split_by_role(payloads: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Partition resolved places into (stops, services) using the taxonomy.

    An unknown code counts as a stop: the taxonomy only ever gains codes, and a
    place we know nothing about is more honest as a destination than as a café.
    """
    from domain import taxonomy

    stops: list[dict[str, Any]] = []
    services: list[dict[str, Any]] = []
    for payload in payloads:
        try:
            is_service = taxonomy.role(payload["category"]) == "service"
        except Exception:  # unknown/unmapped code → a destination, as elsewhere
            is_service = False
        (services if is_service else stops).append(payload)
    return stops, services


def resolve_itineraries(
    conn: Any, items: list[dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Attach dataset facts to every stop, in the authored order.

    Returns the payloads and the list of keys that did not resolve. Two
    itineraries may share a stop (the pharmacy-museum is in two of them), which is
    why every row is fetched once for the whole file.

    **A service is served beside the route, never as a stop.** The curated file
    is hand-written, and one of its routes («С детьми: замки и парк») lists a
    toilet among its stops; returning that as a numbered stop would put a toilet
    where the guide promised a sight. The taxonomy decides (the same
    ``role == "service"`` split the planner uses), so the toilet comes back in
    ``services`` — findable by the tourist, not counted as a destination — and
    the authored file keeps its own keys.
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
                # Curated visit time of the stops themselves; the walking or
                # driving time is added by the router when the route is drawn.
                "visit_minutes": visit_minutes,
                "stops": stops,
                # Secondary points the author put on the way: cafés, toilets.
                # They are not numbered and do not count towards the visit time.
                "services": services,
            }
        )
    return out, missing

"""The full point catalogue — every place in the dataset, for the «все точки» tab.

A raw browse of the dataset, not a query-driven search.
"""

from __future__ import annotations

import logging
from typing import Any

from db.store.place_fields import parse_fun_facts, parse_links, parse_photo

log = logging.getLogger(__name__)

MAX_PLACES = 5000

_COLS = (
    "id, source_url, name, category, town, district, lat, lon, "
    "visit_minutes, opening_hours, blurb, fun_fact, fun_facts, links, "
    "ticket_price, photo_url, photo_author, photo_license, photo_source"
)


def place_payload(row: dict[str, Any]) -> dict[str, Any]:
    """One place, in the shape the panel prints. Numbers stay numbers.

    The keys match `ItineraryStop` on the client.
    """
    return {
        "place_id": row["id"],
        "source_url": row["source_url"],
        "name": row["name"],
        "category": row["category"],
        "town": row["town"],
        "district": row["district"],
        "lat": row["lat"],
        "lon": row["lon"],
        "visit_minutes": row["visit_minutes"],
        "opening_hours": row["opening_hours"],
        "blurb": row["blurb"],
        "fun_fact": row["fun_fact"],
        "fun_facts": parse_fun_facts(row.get("fun_facts")),
        "links": parse_links(row.get("links")),
        "photo": parse_photo(row),
        "ticket_price": row.get("ticket_price"),
    }


def list_places(conn: Any) -> dict[str, Any]:
    """Every place with coordinates, in a stable browse order.

    Ordered by category then town then name, so a re-seed does not reorder it.
    """
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {_COLS} FROM places "
            "WHERE lat IS NOT NULL AND lon IS NOT NULL "
            "ORDER BY category, town, name "
            "LIMIT %s",
            (MAX_PLACES,),
        )
        rows = [dict(row) for row in cur.fetchall()]

    items = [place_payload(row) for row in rows]
    log.info("places: %d points in the catalogue", len(items))
    return {
        "items": items,
        "total": len(items),
        "capped": len(items) >= MAX_PLACES,
    }

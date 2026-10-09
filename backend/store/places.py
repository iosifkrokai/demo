"""The full point catalogue — every place in the dataset, for the «все точки» tab.

Retrieval (`planner/retrieve.py`) is query-driven and capped: it answers «what
serves this request», not «what is there». This module is the other half — a raw
browse of the dataset, so the tourist can see everything at once (every castle,
every bus stop, every café) without asking the model for a plan. It is a
catalogue, not a search.

One shared payload rule: a place is described by `place_payload` here, and the
ready-made routes (`itineraries.py`) reuse it, so a card and a planned route
cannot disagree about the same place.
"""

from __future__ import annotations

import logging
from typing import Any

from store.place_fields import parse_fun_facts, parse_links, parse_photo

log = logging.getLogger(__name__)

#: One browse loads the whole region; the cap is a backstop against a runaway
#: query, not a feature — the dataset is ~2.5k rows and fits in one answer.
MAX_PLACES = 5000

#: Columns a card prints. Chosen explicitly instead of `SELECT *`, like the
#: itinerary stop query — the two must stay in step, since both feed the same
#: `place_payload`.
_COLS = (
    "id, source_url, name, category, town, district, lat, lon, "
    "visit_minutes, opening_hours, blurb, fun_fact, fun_facts, links, "
    "ticket_price, photo_url, photo_author, photo_license, photo_source"
)


def place_payload(row: dict[str, Any]) -> dict[str, Any]:
    """One place, in the shape the panel prints. Numbers stay numbers.

    `fun_facts` and `links` are text columns holding JSON; they are read with the
    shared `store.place_fields` parsers so a card and a planned route cannot
    disagree about the same place. The keys match `ItineraryStop` on the client.
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

    Ordered by category then town then name, so the panel's groups come back
    already sorted and a re-seed does not reorder the list.
    """
    from psycopg.rows import dict_row  # local: keeps the import surface small

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

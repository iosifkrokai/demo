"""What is actually in the database right now — a read-only inventory snapshot.

After reseeds and partial failures the first operational question is "what do we
have": how many places survived, which enrichment fields got filled, which
categories dominate, and whether any rows landed outside the project area.  This
script answers it in one pass over the database without writing anything:

    python scripts/data_inventory.py            # human-readable, stable order
    python scripts/data_inventory.py --json     # machine-readable

Only SELECTs run.  The connection is the backend's own — agent.config reads
DATABASE_URL from the environment, and this script reuses that single source
(settings.DSN); there is no separate config here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import settings

# Rough lon/lat frame around Grodno Oblast: the min/max of the polygon rings in
# data/grodno_border.json (23.508..26.704 E, 52.745..55.004 N) rounded OUT by
# ~0.02°.  This is a cheap rectangle for the question "how many rows are clearly
# outside the project area" — it is NOT the oblast boundary and must not be used
# as one.  The exact predicate (polygon + documented border exceptions) is
# agent.areas.in_project_area; use it when precision matters.
GRODNO_OBLAST_BBOX: tuple[float, float, float, float] = (
    23.49,  # min_lon
    52.72,  # min_lat
    26.73,  # max_lon
    55.03,  # max_lat
)

CATEGORY_TOP_N = 10


# ── SQL builders (pure, so they can be tested against a mock connection) ─────


def places_overview_sql() -> str:
    """One pass over places: totals, field fill rates, outside-bbox count."""
    return """
SELECT
    count(*)                                                 AS total,
    count(*) FILTER (WHERE category IS NOT NULL
                          AND btrim(category) <> '')         AS with_category,
    -- lat/lon are NOT NULL in the schema; NULL or (0,0) means the row carries
    -- no usable coordinate (legacy imports before that constraint).
    count(*) FILTER (WHERE lat IS NULL OR lon IS NULL
                          OR (lat = 0 AND lon = 0))          AS without_coordinates,
    count(*) FILTER (WHERE opening_hours IS NOT NULL
                          AND btrim(opening_hours) <> '')    AS with_opening_hours,
    count(*) FILTER (WHERE fun_facts IS NOT NULL
                          AND btrim(fun_facts) <> '')        AS with_fun_facts,
    count(*) FILTER (WHERE links IS NOT NULL
                          AND btrim(links) <> '')            AS with_links,
    -- Cheap rectangle test on the frame above, not the oblast boundary.
    count(*) FILTER (WHERE lon < %(min_lon)s OR lon > %(max_lon)s
                          OR lat < %(min_lat)s OR lat > %(max_lat)s)
                                                             AS outside_grodno_bbox
FROM places
"""


def category_counts_sql() -> str:
    """Category distribution, biggest first; ties broken by name for stability."""
    return """
SELECT category, count(*) AS n
FROM places
WHERE category IS NOT NULL AND btrim(category) <> ''
GROUP BY category
ORDER BY n DESC, category ASC
LIMIT %(limit)s
"""


def distinct_categories_sql() -> str:
    return """
SELECT count(DISTINCT category)
FROM places
WHERE category IS NOT NULL AND btrim(category) <> ''
"""


def client_tables_sql() -> str:
    """Client tables that actually exist in this database (discovery, not a list)."""
    return """
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND starts_with(table_name, 'client')
ORDER BY table_name
"""


def table_count_sql(table: str) -> sql.Composed:
    # Table names cannot be bound as parameters — compose as an identifier.
    # The name comes from information_schema above, not from user input.
    return sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))


def bbox_params() -> dict[str, float]:
    min_lon, min_lat, max_lon, max_lat = GRODNO_OBLAST_BBOX
    return {
        "min_lon": min_lon,
        "min_lat": min_lat,
        "max_lon": max_lon,
        "max_lat": max_lat,
    }


# ── Report collection ────────────────────────────────────────────────────────


def is_outside_bbox(
    lat: float | None,
    lon: float | None,
    bbox: tuple[float, float, float, float] = GRODNO_OBLAST_BBOX,
) -> bool:
    """True when the point is clearly outside the frame.

    None coordinates answer False — "unknown" is not the same as "outside"
    (the SQL FILTER above behaves the same way: NULL comparisons are not true).
    """
    if lat is None or lon is None:
        return False
    min_lon, min_lat, max_lon, max_lat = bbox
    return lon < min_lon or lon > max_lon or lat < min_lat or lat > max_lat


def collect_report(conn) -> dict:
    """Run every inventory query over an open connection and build the report."""
    with conn.cursor() as cur:
        cur.execute(places_overview_sql(), bbox_params())
        (
            total,
            with_category,
            without_coordinates,
            with_opening_hours,
            with_fun_facts,
            with_links,
            outside_bbox,
        ) = cur.fetchone()

        cur.execute(category_counts_sql(), {"limit": CATEGORY_TOP_N})
        top_categories = [{"category": category, "count": n} for category, n in cur.fetchall()]

        cur.execute(distinct_categories_sql())
        (distinct_categories,) = cur.fetchone()

        cur.execute(client_tables_sql())
        client_tables = []
        for (table_name,) in cur.fetchall():
            cur.execute(table_count_sql(table_name))
            (rows,) = cur.fetchone()
            client_tables.append({"table": table_name, "rows": rows})

    return {
        "places": {
            "total": total,
            "with_category": with_category,
            "without_category": total - with_category,
            "without_coordinates": without_coordinates,
            "with_opening_hours": with_opening_hours,
            "with_fun_facts": with_fun_facts,
            "with_links": with_links,
            "outside_grodno_bbox": outside_bbox,
            "bbox": list(GRODNO_OBLAST_BBOX),
            "distinct_categories": distinct_categories,
            "top_categories": top_categories,
        },
        "client_tables": client_tables,
    }


# ── Rendering ─────────────────────────────────────────────────────────────────


def render_report(report: dict) -> str:
    """Human-readable text; the line order is fixed by this function alone."""
    p = report["places"]
    lines: list[str] = []
    lines.append(f"places: {p['total']} total")
    for label, value in [
        ("with category", p["with_category"]),
        ("without category", p["without_category"]),
        ("without coordinates", p["without_coordinates"]),
        ("with opening_hours", p["with_opening_hours"]),
        ("with fun_facts", p["with_fun_facts"]),
        ("with links", p["with_links"]),
        ("outside grodno_bbox", p["outside_grodno_bbox"]),
    ]:
        lines.append(f"  {label:<24}{value}")
    min_lon, min_lat, max_lon, max_lat = p["bbox"]
    lines.append(
        "    bbox is a rough frame, not the oblast boundary: "
        f"lon {min_lon}..{max_lon}, lat {min_lat}..{max_lat}"
    )
    lines.append("")
    lines.append(f"categories: top {CATEGORY_TOP_N} of {p['distinct_categories']} distinct")
    for entry in p["top_categories"]:
        lines.append(f"  {entry['count']:>6}  {entry['category']}")
    lines.append("")
    lines.append("client tables")
    if report["client_tables"]:
        for entry in report["client_tables"]:
            lines.append(f"  {entry['table']:<24}{entry['rows']}")
    else:
        lines.append("  none")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Read-only snapshot of what is in the database right now."
    )
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)

    try:
        with psycopg.connect(settings.DSN) as conn:
            report = collect_report(conn)
    except psycopg.OperationalError as exc:
        print(f"cannot connect to the database: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

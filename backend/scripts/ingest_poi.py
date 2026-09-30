#!/usr/bin/env python3
"""
ingest_poi.py — Top up the places table with everyday POIs (cafés, restaurants,
toilets, hotels) from OpenStreetMap for the Grodno region, straight into Postgres.

The existing ingest_osm.py writes a CSV of historic/tourist POIs and load_osm.py
loads it; this script follows the same Overpass + geofence + upsert conventions
but talks to the DB directly and uses the ``osm_poi:`` source_url prefix (the
table has no separate ``source`` column — the prefix is how rows are told apart,
matching the ``city:`` / ``region:`` / ``osm:`` prefixes already in use).

Usage
-----
    # full run (Overpass over the whole voblast answers in ~3 min — normal)
    DATABASE_URL=... python scripts/ingest_poi.py
    python scripts/ingest_poi.py --no-embed        # skip embeddings
    python scripts/ingest_poi.py --dry-run         # built-in mock, nothing written
    python scripts/ingest_poi.py --limit 50        # sample
    python scripts/ingest_poi.py --input-json /tmp/poi_overpass.json   # reuse a saved response

Idempotent: rows are keyed on source_url (osm_poi:<type>/<id>) AND a 150 m
name-proximity check skips anything the DB already holds, so a second run
inserts 0 rows.

Exit codes: 0 = success, 1 = network/parse error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import psycopg

try:
    import httpx
except ImportError:
    sys.stderr.write("httpx required: install the project dependencies\n")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from ingest_osm import (
    OVERPASS_ENDPOINTS,
    OVERPASS_HTTP_TIMEOUT_S,
    haversine_m,
    resolve_district,
)

from agent.config import DSN
from agent.geofence import inside_project_area

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Grodno region bbox (S W N E) — same box as agent/constants.py GRODNO_BBOX.
DEFAULT_BBOX = (52.75, 23.35, 54.80, 27.00)

# source_url prefix for every row this script writes.
SOURCE_PREFIX = "osm_poi:"

# Embeddings — mirrors enrich_places.py: same model, same 20-item batch size.
OPENROUTER_URL = "https://openrouter.ai/api/v1/embeddings"
OPENROUTER_EMBED_MODEL = os.environ.get("OPENROUTER_EMBED_MODEL", "openai/text-embedding-3-small")
EMBED_BATCH = 20  # OpenRouter batch limit

# Same-name rows closer than this are the same physical POI (mirrors the
# de-dup threshold named in the task and agent/constants.py DUPLICATE_RADIUS_M).
DEDUP_RADIUS_M = 150.0

# POIs the bbox covers but that live outside the project area are rejected by
# inside_project_area() — without it the box drags in Vilnius and Polish cafés.

POI_QUERY = """
[out:json][timeout:300];
(
  // Coffee and quick food
  nwr["amenity"="cafe"]({south},{west},{north},{east});
  nwr["amenity"="fast_food"]({south},{west},{north},{east});
  // Sit-down restaurants
  nwr["amenity"="restaurant"]({south},{west},{north},{east});
  // Public toilets
  nwr["amenity"="toilets"]({south},{west},{north},{east});
  // Accommodation
  nwr["tourism"~"hotel|hostel|guest_house"]({south},{west},{north},{east});
  // Public-transport boarding points: the tourist walking a route should see
  // where to get on a bus, trolleybus or tram instead of walking the whole way.
  nwr["highway"="bus_stop"]({south},{west},{north},{east});
  nwr["public_transport"="platform"]({south},{west},{north},{east});
  nwr["railway"="tram_stop"]({south},{west},{north},{east});
);
out center;
"""

# OSM tag → project category. Categories already live in agent/constants.CATEGORIES.
AMENITY_CATEGORY = {
    "cafe": "кафе",
    "fast_food": "кафе",
    "restaurant": "ресторан",
    "toilets": "туалет",
}
TOURISM_CATEGORY = {
    "hotel": "гостиница",
    "hostel": "гостиница",
    "guest_house": "гостиница",
}
# Public-transport boarding points. Three OSM spellings mean the same thing to a
# tourist standing at the kerb: a stop you can board. They are services, not sights
# — nothing here is «visited», it is where the walk can be cut short.
HIGHWAY_CATEGORY = {
    "bus_stop": "остановка транспорта",
}
PUBLIC_TRANSPORT_CATEGORY = {
    "platform": "остановка транспорта",
}
RAILWAY_CATEGORY = {
    "tram_stop": "остановка транспорта",
}

# Mock fixture for --dry-run (a handful of realistic Grodno-region POIs).
MOCK_OVERPASS_RESPONSE = {
    "elements": [
        {
            "type": "node",
            "id": 900000001,
            "lat": 53.6772,
            "lon": 23.8232,
            "tags": {"name": "Кафе Ласточка", "amenity": "cafe", "addr:city": "Гродно"},
        },
        {
            "type": "node",
            "id": 900000002,
            "lat": 53.6779,
            "lon": 23.8311,
            "tags": {"name:ru": "Кофейня Зерно", "amenity": "cafe", "addr:city": "Гродно"},
        },
        {
            "type": "node",
            "id": 900000003,
            "lat": 53.6801,
            "lon": 23.8280,
            "tags": {"name": "Ресторан Неман", "amenity": "restaurant", "addr:city": "Гродно"},
        },
        {
            # Unnamed toilet → gets the "Туалет" fallback name
            "type": "node",
            "id": 900000004,
            "lat": 53.6788,
            "lon": 23.8255,
            "tags": {"amenity": "toilets", "addr:street": "ул. Советская"},
        },
        {
            "type": "way",
            "id": 900000005,
            "center": {"lat": 53.8886, "lon": 25.3028},
            "tags": {"name": "Отель Лида", "tourism": "hotel", "addr:city": "Лида"},
        },
        {
            # Outside Belarus — must be cut by the geofence
            "type": "node",
            "id": 900000006,
            "lat": 54.6872,
            "lon": 25.2797,
            "tags": {"name": "Cafe Vilnius", "amenity": "cafe"},
        },
    ]
}


# ---------------------------------------------------------------------------
# Overpass
# ---------------------------------------------------------------------------

def fetch_overpass(bbox: tuple, limit: int | None = None, dry_run: bool = False) -> list | None:
    """Query Overpass with retry+backoff, or return the mock fixture.

    Same endpoint list and dead-endpoint handling as ingest_osm.fetch_overpass.
    Returns None when every endpoint failed.
    """
    if dry_run:
        elements = MOCK_OVERPASS_RESPONSE["elements"]
        return elements[:limit] if limit else elements

    south, west, north, east = bbox
    query = POI_QUERY.format(south=south, west=west, north=north, east=east)
    request_timeout = httpx.Timeout(OVERPASS_HTTP_TIMEOUT_S, connect=30.0)

    dead: set[str] = set()
    for attempt in range(4):
        for endpoint in OVERPASS_ENDPOINTS:
            if endpoint in dead:
                continue
            try:
                resp = httpx.post(
                    endpoint,
                    data={"data": query},
                    timeout=request_timeout,
                    headers={"User-Agent": "GrodnoRegionPipeline/1.0 (codex-ai)"},
                )
                resp.raise_for_status()
                elements = resp.json().get("elements", [])
                return elements[:limit] if limit else elements
            except Exception as exc:
                sys.stderr.write(f"[overpass] attempt {attempt + 1}/{endpoint}: {exc}\n")
                if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
                                    httpx.ConnectTimeout, httpx.RemoteProtocolError)):
                    dead.add(endpoint)

        if all(ep in dead for ep in OVERPASS_ENDPOINTS):
            break
        if attempt < 3:
            wait = (2 ** attempt) * 5
            sys.stderr.write(f"[overpass] retry in {wait}s …\n")
            time.sleep(wait)

    sys.stderr.write("[overpass] all endpoints failed after 4 attempts — exiting\n")
    return None


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------

def tag_to_category(tags: dict) -> str | None:
    """Map OSM amenity/tourism/transit tags → project category (or None)."""
    amenity = tags.get("amenity", "")
    if amenity in AMENITY_CATEGORY:
        return AMENITY_CATEGORY[amenity]
    tourism = tags.get("tourism", "")
    if tourism in TOURISM_CATEGORY:
        return TOURISM_CATEGORY[tourism]
    highway = tags.get("highway", "")
    if highway in HIGHWAY_CATEGORY:
        return HIGHWAY_CATEGORY[highway]
    if tags.get("public_transport", "") in PUBLIC_TRANSPORT_CATEGORY:
        return PUBLIC_TRANSPORT_CATEGORY[tags["public_transport"]]
    railway = tags.get("railway", "")
    if railway in RAILWAY_CATEGORY:
        return RAILWAY_CATEGORY[railway]
    return None


def extract_name(tags: dict, category: str) -> str | None:
    """Name: ``name`` else ``name:ru``.

    Toilets and transit stops are allowed to be anonymous — most OSM bus stops
    carry no name, and dropping them would hide exactly the boarding point the
    tourist needs. They fall back to «Туалет» / «Остановка», with the street
    appended when OSM knows it. Everything else without a name is dropped.
    """
    name = tags.get("name") or tags.get("name:ru")
    if name:
        return name.strip()
    if category in ("туалет", "остановка транспорта"):
        label = "Туалет" if category == "туалет" else "Остановка"
        street = (tags.get("addr:street") or "").strip()
        return f"{label} ({street})" if street else label
    return None


def osm_element_to_row(element: dict) -> dict | None:
    """Convert one Overpass element → a places row, or None when it is filtered out."""
    tags = element.get("tags", {})
    if element.get("type") == "node":
        lat, lon = element.get("lat"), element.get("lon")
    else:
        center = element.get("center", {})
        lat, lon = center.get("lat"), center.get("lon")
    if lat is None or lon is None:
        return None

    category = tag_to_category(tags)
    if not category:
        return None

    name = extract_name(tags, category)
    if not name:
        return None

    # The bbox covers a slice of Lithuania and Poland — the border polygon decides
    # what is actually in the project area.
    if not inside_project_area(float(lat), float(lon)):
        return None

    osm_type = element.get("type", "unknown")
    osm_id = element.get("id")
    return {
        "name": name,
        "category": category,
        "lat": float(lat),
        "lon": float(lon),
        "town": tags.get("addr:city") or tags.get("addr:village") or "",
        "opening_hours": tags.get("opening_hours", ""),
        "ticket_price": tags.get("charge") or tags.get("fee", ""),
        "source_url": f"{SOURCE_PREFIX}{osm_type}/{osm_id}",
    }


def collect_rows(elements: list) -> tuple[list[dict], dict[str, int]]:
    """Map Overpass elements → rows, tallying everything the filters drop."""
    rows: list[dict] = []
    counts = {"geo_rejected": 0, "nameless": 0, "unmapped": 0}
    for el in elements:
        tags = el.get("tags", {})
        category = tag_to_category(tags)
        if category is None:
            counts["unmapped"] += 1
            continue
        if extract_name(tags, category) is None:
            counts["nameless"] += 1
            continue
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center", {})
            lat, lon = center.get("lat"), center.get("lon")
        if lat is not None and lon is not None and not inside_project_area(float(lat), float(lon)):
            counts["geo_rejected"] += 1
            continue
        row = osm_element_to_row(el)
        if row:
            rows.append(row)
    return rows, counts


def is_name_dup(row: dict, existing: list[dict], threshold_m: float = DEDUP_RADIUS_M) -> bool:
    """True when an existing place shares the exact name within threshold_m.

    Same rule as ingest_osm.deduplicate / load_osm.is_duplicate — an OSM row and
    its curated twin usually carry the same name a few dozen metres apart.
    """
    for ex in existing:
        if ex["name"] == row["name"] and haversine_m(
            row["lat"], row["lon"], float(ex["lat"]), float(ex["lon"])
        ) < threshold_m:
            return True
    return False


def dedup_rows(rows: list[dict], existing: list[dict]) -> tuple[list[dict], int]:
    """Keep rows whose (name, 150 m) is not already held by the DB or an earlier row."""
    to_insert: list[dict] = []
    duplicates = 0
    for row in rows:
        if is_name_dup(row, existing) or is_name_dup(row, to_insert):
            duplicates += 1
        else:
            to_insert.append(row)
    return to_insert, duplicates


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def load_existing_places(conn) -> list[dict]:
    """name/lat/lon of every place, for the 150 m name de-dup."""
    with conn.cursor() as cur:
        cur.execute("SELECT name, lat, lon FROM places")
        return [{"name": r[0], "lat": r[1], "lon": r[2]} for r in cur.fetchall()]


# Same column set as load_osm/seed_region; visit_minutes stays NULL — the planner
# derives visit time per category in planner/cost.py.
UPSERT_SQL = """
    INSERT INTO places (name, category, district, town, lat, lon,
                        opening_hours, ticket_price, visit_minutes, source_url)
    VALUES (%(name)s, %(category)s, %(district)s, %(town)s, %(lat)s, %(lon)s,
            %(opening_hours)s, %(ticket_price)s, %(visit_minutes)s, %(source_url)s)
    ON CONFLICT (source_url) DO UPDATE SET
        name = EXCLUDED.name, category = EXCLUDED.category,
        district = EXCLUDED.district, town = EXCLUDED.town,
        lat = EXCLUDED.lat, lon = EXCLUDED.lon,
        opening_hours = EXCLUDED.opening_hours, ticket_price = EXCLUDED.ticket_price,
        visit_minutes = EXCLUDED.visit_minutes
"""


def insert_rows(conn, rows: list[dict]) -> int:
    """Upsert rows keyed on source_url. Returns the number of truly new rows."""
    inserted = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute("SELECT 1 FROM places WHERE source_url = %s", (row["source_url"],))
            was_new = cur.fetchone() is None
            cur.execute(UPSERT_SQL, row)
            if was_new:
                inserted += 1
    conn.commit()
    return inserted


def _openrouter_embed(texts: list[str]) -> list[list[float]]:
    """Call the OpenRouter embeddings API (same call as enrich_places.py)."""
    api_key = os.environ["OPENROUTER_API_KEY"]
    resp = httpx.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": OPENROUTER_EMBED_MODEL, "input": texts},
        timeout=60.0,
    )
    resp.raise_for_status()
    # JSON numbers arrive as int when a dimension is exactly 0 or 1; a list mixing
    # int and float is what psycopg refuses to adapt («cannot dump lists of mixed
    # types»), and the failure is data-dependent (it only fires once such a vector
    # shows up, mid-run). Coerce here, where the declared type says float anyway.
    return [[float(x) for x in item["embedding"]] for item in resp.json()["data"]]


def embed_new_rows(conn) -> int:
    """Embed osm_poi rows with a NULL vector.

    This is enrich_places.py's embedding mechanism (same ``_openrouter_embed``
    call, same model + 20-item batches, same ``name. description`` text), kept
    inline because importing enrich_places pulls agent.llm, which no longer
    exists in this tree.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("[ingest_poi] OPENROUTER_API_KEY not set — skipping embeddings")
        return 0

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, description FROM places "
            "WHERE source_url LIKE %s AND embedding IS NULL ORDER BY id",
            (SOURCE_PREFIX + "%",),
        )
        pending = cur.fetchall()
    if not pending:
        print("[ingest_poi] no new rows need embedding")
        return 0

    print(f"[ingest_poi] embedding {len(pending)} rows via OpenRouter...")
    done = 0
    for i in range(0, len(pending), EMBED_BATCH):
        batch = pending[i:i + EMBED_BATCH]
        texts = [f"{(n or '').strip()}. {(d or '').strip()}".strip(" .") for _, n, d in batch]
        vecs = _openrouter_embed(texts)
        with conn.cursor() as cur:
            for (pid, _, _), vec in zip(batch, vecs, strict=True):
                cur.execute(
                    "UPDATE places SET embedding = %s::vector WHERE id = %s",
                    (vec, pid),
                )
        conn.commit()
        done += len(batch)
        print(f"[ingest_poi]   embedded {done}/{len(pending)}", flush=True)
    return done


def print_category_breakdown(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT category, count(*) FROM places GROUP BY 1 ORDER BY 2 DESC")
        print("[ingest_poi] places by category:")
        for category, count in cur.fetchall():
            print(f"    {count:6d}  {category}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest everyday OSM POIs into places")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"),
                        help="Bounding box south west north east")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max elements to process (for testing)")
    parser.add_argument("--input-json", type=Path, default=None,
                        help="Reuse a saved Overpass response instead of hitting the API")
    parser.add_argument("--dry-run", action="store_true",
                        help="Use the built-in mock fixture instead of the network")
    parser.add_argument("--no-embed", action="store_true",
                        help="Skip embedding computation")
    args = parser.parse_args()

    bbox = tuple(args.bbox) if args.bbox else DEFAULT_BBOX
    print(f"[ingest_poi] bbox={bbox}, dry_run={args.dry_run}, limit={args.limit}")

    # 1. Fetch elements
    if args.input_json is not None:
        elements = json.loads(args.input_json.read_text(encoding="utf-8")).get("elements", [])
        print(f"[ingest_poi] loaded {len(elements)} elements from {args.input_json}")
    else:
        elements = fetch_overpass(bbox, limit=args.limit, dry_run=args.dry_run)
    if elements is None:
        sys.stderr.write("[ingest_poi] fatal: all Overpass endpoints failed\n")
        sys.exit(1)
    if args.limit:
        elements = elements[:args.limit]
    fetched = len(elements)
    print(f"[ingest_poi] fetched {fetched} elements")

    # 2. Map tags → rows, counting what the geofence (and tag filter) drops.
    rows, counts = collect_rows(elements)
    rejected_geo = counts["geo_rejected"]
    print(f"[ingest_poi] {len(rows)} rows after tag/name filtering "
          f"(geo-rejected={rejected_geo}, nameless-skipped={counts['nameless']}, "
          f"unmapped-skipped={counts['unmapped']})")

    # 3. Districts — nearest raion centre, same as ingest_osm.py's default.
    nominatim_cache: dict = {}
    for row in rows:
        row["district"] = resolve_district(row["lat"], row["lon"], {}, nominatim_cache,
                                           use_nominatim=False)

    # 4. De-dup against the DB (name within 150 m) + against each other.
    with psycopg.connect(DSN) as conn:
        existing = load_existing_places(conn)
    print(f"[ingest_poi] {len(existing)} existing places loaded for de-dup")

    to_insert, duplicates = dedup_rows(rows, existing)
    print(f"[ingest_poi] duplicates={duplicates}, to-insert={len(to_insert)}")

    inserted = 0
    embedded = 0
    if args.dry_run:
        for row in to_insert:
            print(f"  [dry-run] {row['name']} ({row['category']}, {row['district']})")
    else:
        # visit_minutes stays NULL — cost.py derives it from the category.
        for row in to_insert:
            row["visit_minutes"] = None
        with psycopg.connect(DSN) as conn:
            inserted = insert_rows(conn, to_insert)
            # Embed any osm_poi row still lacking a vector (covers a re-run that
            # inserted 0 but left earlier rows un-embedded).
            if not args.no_embed:
                embedded = embed_new_rows(conn)

    # 5. Summary
    with psycopg.connect(DSN) as conn:
        print_category_breakdown(conn)

    print(f"\n[ingest_poi] SUMMARY: fetched={fetched}, geo-rejected={rejected_geo}, "
          f"nameless-skipped={counts['nameless']}, unmapped-skipped={counts['unmapped']}, "
          f"duplicates={duplicates}, inserted={inserted}, embedded={embedded}")


if __name__ == "__main__":
    main()

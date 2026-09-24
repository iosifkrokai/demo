#!/usr/bin/env python3
"""
ingest_osm.py — Pull historic/tourist/religious POIs from Overpass API
into backend/data/places_osm_raw.csv (same pipe-separated format as places_region.csv).

Usage
-----
    python backend/scripts/ingest_osm.py                          # full bbox
    python backend/scripts/ingest_osm.py --limit 50              # sample
    python backend/scripts/ingest_osm.py --dry-run               # mock fixture
    python backend/scripts/ingest_osm.py --bbox 23.0 52.0 28.0 55.0
    python backend/scripts/ingest_osm.py --output /tmp/test.csv

Overpass endpoints are tried in order with exponential-backoff retry.
Nominatim reverse lookups are rate-limited to 1 req/s and cached to
backend/.cache/nominatim/ as one JSON file per tile (z8).

Exit codes: 0 = success, 1 = network/parse error.
"""

import argparse
import csv
import json
import math
import os
import sys
import time
import hashlib
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    sys.stderr.write("httpx required: pip install httpx\n")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BBOX = (23.35, 52.75, 27.00, 54.80)  # Grodno region (W S E N)
SCRIPT_DIR = Path(__file__).parent.resolve()
CACHE_DIR = SCRIPT_DIR.parent / ".cache" / "nominatim"
OUT_DIR = SCRIPT_DIR.parent / "data"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Client-side timeout for an Overpass POST. Must be >= the query's own
# [timeout:] (measured: 172 s for the full voblast bbox against
# overpass-api.de). kumi.systems / maps.mail.ru were unreachable (25 s+
# connect timeouts) when this was written — the retry loop marks such
# endpoints dead for the rest of the run instead of paying that cost again.
OVERPASS_HTTP_TIMEOUT_S = 360.0

OVERPASS_QUERY = """
[out:json][timeout:300];
(
  // Castles, manors, monasteries, memorials, monuments, ruins
  nwr["historic"~"castle|manor|monastery|memorial|monument|ruins|archaeological_site"]({south},{west},{north},{east});
  // Museums and attractions
  nwr["tourism"~"museum|attraction"]({south},{west},{north},{east});
  // Places of worship
  nwr["amenity"="place_of_worship"]({south},{west},{north},{east});
  // Church / castle buildings
  nwr["building"~"church|castle"]({south},{west},{north},{east});
);
out center;
"""

# Category → visit_minutes lookup
VISIT_MINUTES = {
    "замок": 40,
    "музей": 40,
    "монастырь": 30,
    "дворец": 30,
    "усадьба": 30,
    "парк": 30,
    "костёл": 20,
    "церковь": 20,
    "храм": 20,
    "архитектура": 20,
    "кладбище": 15,
    "памятник": 10,
    "инфраструктура": 10,
}

# District centroids (raion centres) as fallback
RAION_CENTRES = {
    "Гродненский": (53.6688, 23.8380),
    "Берестовицкий": (53.4420, 24.0353),
    "Волковысский": (53.1635, 24.4514),
    "Свислочский": (52.9296, 24.0939),
    "Мостовский": (53.4134, 24.5365),
    "Щучинский": (53.6055, 24.7406),
    "Островецкий": (54.6045, 25.9589),
    "Ошмянский": (54.4120, 25.7946),
    "Сморгонский": (54.4784, 26.3967),
    "Ивьевский": (54.1639, 25.7845),
    "Лидский": (53.8886, 25.3028),
    "Кореличский": (53.6010, 26.3025),
    "Новогрудский": (53.5958, 25.8269),
    "Дятловский": (53.4522, 25.4618),
    "Зельвенский": (53.1480, 24.8190),
    "Вороновский": (54.2560, 25.3032),
}

# ---------------------------------------------------------------------------
# Mock fixture for --dry-run (5 realistic Grodno-region elements)
# ---------------------------------------------------------------------------

MOCK_OVERPASS_RESPONSE = {
    "elements": [
        {
            "type": "node",
            "id": 123456789,
            "lat": 53.6823,
            "lon": 23.8341,
            "tags": {
                "name": "Коложская церковь",
                "name:ru": "Коложская церковь",
                "name:be": "Калажская царква",
                "building": "church",
                "amenity": "place_of_worship",
                "religion": "christian",
                "denomination": "orthodox",
                "start_date": "XII",
            },
        },
        {
            "type": "way",
            "id": 234567890,
            "center": {"lat": 53.1100, "lon": 24.4500},
            "tags": {
                "name": "Замок в Мире",
                "name:ru": "Замок в Мире",
                "name:en": "Mir Castle",
                "historic": "castle",
                "building": "yes",
                "tourism": "attraction",
            },
        },
        {
            "type": "node",
            "id": 345678901,
            "lat": 53.9321,
            "lon": 25.3012,
            "tags": {
                "name": "Фарный костёл",
                "name:ru": "Фарный костёл",
                "amenity": "place_of_worship",
                "religion": "christian",
                "denomination": "catholic",
                "building": "church",
            },
        },
        {
            "type": "node",
            "id": 456789012,
            "lat": 54.1034,
            "lon": 25.3138,
            "tags": {
                "name": "Монастырь бернардинцев",
                "name:ru": "Монастырь бернардинцев",
                "historic": "monastery",
                "amenity": "place_of_worship",
                "religion": "christian",
            },
        },
        {
            "type": "way",
            "id": 567890123,
            "center": {"lat": 53.6887, "lon": 23.8250},
            "tags": {
                "name": "Новый замок",
                "name:ru": "Новый замок",
                "name:be": "Новы замак",
                "historic": "castle",
                "building": "castle",
                "tourism": "yes",
            },
        },
    ]
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def nominatim_tile(lat: float, lon: float) -> tuple[int, int]:
    """z8 tile coordinates for Nominatim tile cache."""
    x = int((lon + 180) / 360 * 256)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * 256)
    return x, y


def nominatim_cache_path(lat: float, lon: float) -> Path:
    x, y = nominatim_tile(lat, lon)
    return CACHE_DIR / f"tile_z8_{x}_{y}.json"


def nominatim_reverse(lat: float, lon: float, cache: dict) -> str | None:
    """Reverse geocode via disk cache + Nominatim API (1 rps)."""
    key = (round(lat, 4), round(lon, 4))
    if key in cache:
        return cache[key]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tile_path = nominatim_cache_path(lat, lon)
    tile_cache: dict = {}
    if tile_path.exists():
        try:
            tile_cache = json.loads(tile_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if str(key) in tile_cache:
        cache[key] = tile_cache[str(key)]
        return cache[key]

    url = "https://nominatim.openstreetmap.org/reverse"
    params = {"lat": lat, "lon": lon, "format": "json", "accept-language": "ru"}
    headers = {"User-Agent": "GrodnoRegionPipeline/1.0 (codex-ai)"}
    try:
        resp = httpx.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        sys.stderr.write(f"[nominatim] {exc}\n")
        return None

    district = None
    addr = data.get("address", {})
    district = addr.get("county") or addr.get("district") or addr.get("region")
    if district:
        district = district.replace(" район", "").replace(" District", "").strip()

    cache[key] = district
    tile_cache[str(key)] = district
    try:
        tile_path.write_text(json.dumps(tile_cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    time.sleep(1.1)  # Nominatim rate limit: 1 req/s
    return district


def resolve_district(
    lat: float,
    lon: float,
    osm_tags: dict,
    cache: dict,
    use_nominatim: bool = True,
) -> str:
    """District: OSM addr:district → Nominatim reverse → nearest raion centre.

    Nominatim costs 1.1 s per distinct coordinate (their usage policy), which is
    ~40 min for a full-voblast run. Callers doing a bulk ingest pass
    use_nominatim=False and get the nearest raion centre instead — the district
    is cosmetic for routing, and the value is normalised to the
    "<Name> район" form the curated rows already use.
    """
    d = osm_tags.get("addr:district", "")
    if d:
        return d.replace(" район", "").strip()

    if use_nominatim:
        district = nominatim_reverse(lat, lon, cache)
        if district:
            return district

    nearest = min(RAION_CENTRES, key=lambda r: haversine_m(lat, lon, *RAION_CENTRES[r]))
    return f"{nearest} район"


def tag_to_category(tags: dict) -> str | None:
    """Map OSM tags → our category."""
    historic = tags.get("historic", "")
    tourism = tags.get("tourism", "")
    amenity = tags.get("amenity", "")
    building = tags.get("building", "")
    religion = tags.get("religion", "")
    denomination = tags.get("denomination", "")

    if historic in ("castle", "manor", "ruins"):
        return "замок"
    if historic in ("monastery", "monastic_architecture"):
        return "монастырь"
    if historic in ("memorial", "monument"):
        return "памятник"
    if historic in ("archaeological_site",):
        return "архитектура"

    if tourism == "museum":
        return "музей"
    if tourism == "attraction":
        return "архитектура"

    if amenity == "place_of_worship":
        if religion == "christian":
            if denomination in ("catholic", "united", "roman_catholic"):
                return "костёл"
            if denomination in ("orthodox", "russian_orthodox", "old_believer"):
                return "церковь"
            return "костёл"
        if religion == "muslim":
            return "храм"
        if religion == "jewish":
            return "архитектура"
        return "храм"

    if building in ("church", "cathedral", "chapel"):
        if religion == "christian":
            if denomination in ("catholic", "roman_catholic"):
                return "костёл"
            return "церковь"
        return "архитектура"
    if building == "castle":
        return "замок"

    return None


def extract_name(tags: dict) -> str | None:
    """Prefer name:ru > name, skip if no Russian/Cyrillic name."""
    for key in ("name:ru", "name", "name:be"):
        val = tags.get(key, "")
        if val:
            return val
    return None


def deduplicate(items: list[dict], threshold_m: float = 50.0) -> list[dict]:
    """Drop items within threshold_m of an earlier item (by name + coords).

    Coords are CSV-formatted strings at this point (osm_element_to_row writes
    "{lat:.6f}"), so they are coerced here — passing the raw strings into
    haversine_m raises "must be real number, not str".
    """
    seen: list[dict] = []
    for item in items:
        is_dup = False
        for s in seen:
            if (s["name"] == item["name"] and
                    haversine_m(float(s["lat"]), float(s["lon"]),
                                float(item["lat"]), float(item["lon"])) < threshold_m):
                is_dup = True
                break
        if not is_dup:
            seen.append(item)
    return seen


def osm_element_to_row(element: dict) -> dict | None:
    """Convert a single Overpass element → CSV row dict."""
    tags = element.get("tags", {})

    if element.get("type") == "node":
        lat = element.get("lat")
        lon = element.get("lon")
    else:
        center = element.get("center", {})
        lat = center.get("lat")
        lon = center.get("lon")

    if lat is None or lon is None:
        return None

    name = extract_name(tags)
    if not name:
        return None

    # Skip if name has no Cyrillic (no Russian name)
    if not any("\u0400" <= c <= "\u04FF" for c in name):
        return None

    category = tag_to_category(tags)
    if not category:
        return None

    osm_type = element.get("type", "unknown")
    osm_id = element.get("id")
    source_url = f"osm:{osm_type}/{osm_id}"

    return {
        "name": name,
        "category": category,
        "district": "",  # filled later
        "town": tags.get("addr:city") or tags.get("addr:village") or "",
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
        "blurb": "",
        "fun_fact": "",
        "fun_facts": "[]",
        "opening_hours": tags.get("opening_hours", ""),
        "ticket_price": tags.get("charge") or tags.get("fee", ""),
        "visit_minutes": str(VISIT_MINUTES.get(category, 20)),
        "links": "[]",
        "source_url": source_url,
    }


def fetch_overpass(bbox: tuple, limit: int | None = None, dry_run: bool = False) -> list:
    """Query Overpass API with retry+backoff, or return mock data. Returns None on total failure."""
    if dry_run:
        elements = MOCK_OVERPASS_RESPONSE["elements"]
        if limit:
            elements = elements[:limit]
        return elements

    south, west, north, east = bbox
    query = OVERPASS_QUERY.format(south=south, west=west, north=north, east=east)

    # A full-voblast run is heavy: measured 172 s / 6869 elements against
    # overpass-api.de. A 120 s client timeout cuts it off mid-flight and the
    # retry loop then burns hours on the endpoints that are down, so the
    # request timeouts here must exceed the query's own [timeout:] budget.
    request_timeout = httpx.Timeout(OVERPASS_HTTP_TIMEOUT_S, connect=30.0)

    failed_endpoints: list[str] = []
    dead: set[str] = set()  # endpoints that timed out / refused to connect
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
                data = resp.json()
                elements = data.get("elements", [])
                if limit:
                    elements = elements[:limit]
                return elements
            except Exception as exc:
                msg = f"[overpass] attempt {attempt+1}/{endpoint}: {exc}"
                sys.stderr.write(msg + "\n")
                if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
                                    httpx.ConnectTimeout, httpx.RemoteProtocolError)):
                    dead.add(endpoint)  # don't waste another timeout on it this run
                if attempt == 3 and endpoint not in dead:  # last attempt: record for summary
                    failed_endpoints.append(f"{endpoint} ({exc})")

        if all(ep in dead for ep in OVERPASS_ENDPOINTS):
            break
        if attempt < 3:  # no sleep after the final attempt
            wait = (2 ** attempt) * 5
            sys.stderr.write(f"[overpass] retry in {wait}s …\n")
            time.sleep(wait)

    for ep in failed_endpoints:
        sys.stderr.write(f"[overpass] FAILED: {ep}\n")
    sys.stderr.write(f"[overpass] all {len(OVERPASS_ENDPOINTS)} endpoints failed after 4 attempts — exiting\n")
    return None


def write_csv(rows: list[dict], path: Path) -> None:
    """Write rows in pipe-separated CSV format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "name", "category", "district", "town", "lat", "lon",
        "blurb", "fun_fact", "fun_facts", "opening_hours",
        "ticket_price", "visit_minutes", "links", "source_url",
    ]
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# " + "|".join(fieldnames) + "\n")
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="|", quoting=csv.QUOTE_MINIMAL)
        pass  # header already written as comment
        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest OSM POIs for Grodno region")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"),
                        help="Bounding box (west south east north)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max elements to fetch (for testing)")
    parser.add_argument("--output", type=Path,
                        help="Output CSV path (default: backend/data/places_osm_raw.csv)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Use built-in mock fixture instead of network")
    parser.add_argument("--input-json", type=Path, default=None,
                        help="Reuse a saved Overpass response ({\"elements\": [...]}) instead of "
                             "hitting the API — the full-voblast query takes ~3 min and "
                             "overpass-api.de intermittently answers 504")
    parser.add_argument("--district-mode", choices=("raion", "nominatim"), default="raion",
                        help="raion = nearest raion centre (fast, offline); "
                             "nominatim = reverse-geocode each POI (1 req/s, ~40 min for the voblast)")
    args = parser.parse_args()

    bbox = tuple(args.bbox) if args.bbox else DEFAULT_BBOX
    out_path = args.output or (OUT_DIR / "places_osm_raw.csv")
    limit = args.limit
    dry_run = args.dry_run

    print(f"[ingest_osm] bbox={bbox}, limit={limit}, dry_run={dry_run}")
    print(f"[ingest_osm] output={out_path}")

    # 1. Fetch elements (or reuse a previously saved Overpass response)
    if args.input_json is not None:
        saved = json.loads(args.input_json.read_text(encoding="utf-8"))
        elements = saved.get("elements", [])
        print(f"[ingest_osm] loaded {len(elements)} elements from {args.input_json}")
    else:
        elements = fetch_overpass(bbox, limit=limit, dry_run=dry_run)
    if elements is None:
        sys.stderr.write("[ingest_osm] fatal: all Overpass endpoints failed\n")
        sys.exit(1)
    if limit:
        elements = elements[:limit]
    print(f"[ingest_osm] fetched {len(elements)} elements")

    # 2. Convert to rows (name filter + tag mapping)
    rows: list[dict] = []
    for el in elements:
        row = osm_element_to_row(el)
        if row:
            rows.append(row)

    print(f"[ingest_osm] {len(rows)} rows after tag filtering")

    # 3. De-duplicate by name + proximity
    rows = deduplicate(rows)
    print(f"[ingest_osm] {len(rows)} rows after de-dup")

    # 4. Districts: nearest raion centre by default (the voblast has ~thousands of
    #    POIs; Nominatim at 1 req/s would take ~40 min — pass --district-mode
    #    nominatim when accuracy matters more than runtime).
    use_nominatim = args.district_mode == "nominatim"
    nominatim_cache: dict = {}
    for row in rows:
        lat = float(row["lat"])
        lon = float(row["lon"])
        district = resolve_district(lat, lon, {}, nominatim_cache, use_nominatim=use_nominatim)
        row["district"] = district

    print(f"[ingest_osm] resolved districts for {len(rows)} rows")

    # 5. Write output
    write_csv(rows, out_path)
    print(f"[ingest_osm] wrote {out_path}  ({len(rows)} places)")


if __name__ == "__main__":
    main()

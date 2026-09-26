#!/usr/bin/env python3
"""
load_osm.py — Load places from a pipe-format OSM CSV into the places table.

Reads backend/data/places_osm_raw.csv (or --path), validates each row,
deduplicates against existing DB rows (~50 m name-matched), upserts into places
keyed on source_url, and computes embeddings for rows lacking them via OpenRouter.

Usage
-----
    python backend/scripts/load_osm.py                       # apply
    python backend/scripts/load_osm.py --dry-run            # validate + print plan
    python backend/scripts/load_osm.py --limit 10 --dry-run # small test
    python backend/scripts/load_osm.py --no-embed           # skip embeddings
    python backend/scripts/load_osm.py --path /tmp/osm.csv   # custom CSV

Exit codes: 0 = success, 1 = validation errors or network failure.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.geofence import inside_project_area

# ---------------------------------------------------------------------------
# Constants — mirrors seed_region.py
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_CSV = SCRIPT_DIR.parent / "data" / "places_osm_raw.csv"
DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")

# Grodno voblast bbox — same as agent/constants.py GRODNO_BBOX
BBOX = {"south": 52.75, "west": 23.35, "north": 54.80, "east": 27.00}

# Category taxonomy — must match seed_region.py TAXONOMY
TAXONOMY = {
    "замок", "костёл", "церковь", "монастырь", "дворец", "усадьба",
    "парк", "музей", "архитектура", "памятник", "инфраструктура",
    "храм", "кладбище",
}

COLUMNS = [
    "name", "category", "district", "town", "lat", "lon", "blurb",
    "fun_fact", "fun_facts", "opening_hours", "ticket_price",
    "visit_minutes", "links", "source_url",
]

UPSERT_SQL = """
    INSERT INTO places (name, category, district, town, lat, lon, blurb,
                        fun_fact, fun_facts, opening_hours, ticket_price,
                        visit_minutes, links, source_url)
    VALUES (%(name)s, %(category)s, %(district)s, %(town)s, %(lat)s, %(lon)s,
            %(blurb)s, %(fun_fact)s, %(fun_facts)s, %(opening_hours)s,
            %(ticket_price)s, %(visit_minutes)s, %(links)s, %(source_url)s)
    ON CONFLICT (source_url) DO UPDATE SET
        name = EXCLUDED.name, category = EXCLUDED.category,
        district = EXCLUDED.district, town = EXCLUDED.town,
        lat = EXCLUDED.lat, lon = EXCLUDED.lon, blurb = EXCLUDED.blurb,
        fun_fact = EXCLUDED.fun_fact, fun_facts = EXCLUDED.fun_facts,
        opening_hours = EXCLUDED.opening_hours, ticket_price = EXCLUDED.ticket_price,
        visit_minutes = EXCLUDED.visit_minutes, links = EXCLUDED.links
"""

# Embedding
EMBED_MODEL = "openai/text-embedding-3-small"
EMBED_BATCH_SIZE = 20
OPENROUTER_URL = "https://openrouter.ai/api/v1/embeddings"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Metres between two lat/lon points."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def read_csv(path: Path) -> list[dict]:
    """Parse a pipe-delimited CSV (header may be commented out)."""
    lines = [
        ln for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    rows: list[dict] = []
    for raw in csv.reader(lines, delimiter="|"):
        if len(raw) != len(COLUMNS):
            raise SystemExit(
                f"expected {len(COLUMNS)} columns, got {len(raw)}: {raw[:3]}..."
            )
        rows.append(dict(zip(COLUMNS, raw)))
    return rows


def validate(row: dict) -> list[str]:
    """Return a list of problems (empty = valid)."""
    problems: list[str] = []
    if row["category"] not in TAXONOMY:
        problems.append(f"category {row['category']!r} not in taxonomy")
    try:
        lat, lon = float(row["lat"]), float(row["lon"])
    except (ValueError, TypeError):
        return ["lat/lon not numeric"]
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return ["lat/lon not finite"]
    if not inside_project_area(lat, lon):
        problems.append("coordinates outside Grodno region")
    if not (BBOX["south"] <= lat <= BBOX["north"]):
        problems.append(f"lat {lat} outside bbox {BBOX}")
    if not (BBOX["west"] <= lon <= BBOX["east"]):
        problems.append(f"lon {lon} outside bbox {BBOX}")
    try:
        json.loads(row["fun_facts"])
    except json.JSONDecodeError as e:
        problems.append(f"fun_facts bad JSON ({e})")
    try:
        json.loads(row["links"])
    except json.JSONDecodeError as e:
        problems.append(f"links bad JSON ({e})")
    if not row["source_url"].startswith("osm:"):
        problems.append("source_url must start with 'osm:'")
    try:
        int(row["visit_minutes"])
    except ValueError:
        problems.append(f"visit_minutes {row['visit_minutes']!r} not an int")
    return problems


def normalize(row: dict) -> dict:
    """Normalize field values for DB insertion."""
    return {
        "name": row["name"].strip(),
        "category": row["category"].strip() or None,
        "district": row["district"].strip() or None,
        "town": row["town"].strip() or None,
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "blurb": row["blurb"].strip() or None,
        "fun_fact": row["fun_fact"].strip() or None,
        "fun_facts": row["fun_facts"].strip() or None,
        "opening_hours": row["opening_hours"].strip() or None,
        "ticket_price": row["ticket_price"].strip() or None,
        "visit_minutes": int(row["visit_minutes"]),
        "links": row["links"].strip() or None,
        "source_url": row["source_url"].strip(),
    }


def load_existing_places(conn) -> list[dict]:
    """Fetch name/lat/lon for all existing places (for dedup)."""
    with conn.cursor() as cur:
        cur.execute("SELECT name, lat, lon FROM places")
        return [{"name": r[0], "lat": r[1], "lon": r[2]} for r in cur.fetchall()]


def is_duplicate(incoming: dict, existing: list[dict], threshold_m: float = 50.0) -> bool:
    """True if any existing place shares the exact same name within threshold_m."""
    for ex in existing:
        if ex["name"] == incoming["name"]:
            if haversine_m(incoming["lat"], incoming["lon"], ex["lat"], ex["lon"]) < threshold_m:
                return True
    return False


def embed_missing(conn, dry_run: bool = False) -> int:
    """Embed OSM rows lacking vectors via OpenRouter. Returns count embedded."""
    import httpx  # noqa: PLC0415 — only needed here

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("  OPENROUTER_API_KEY not set — skipping embeddings")
        return 0

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, blurb FROM places "
            "WHERE source_url LIKE 'osm:%%' AND embedding IS NULL"
        )
        pending = cur.fetchall()

    if not pending:
        print("  no rows need embedding")
        return 0

    print(f"  embedding {len(pending)} rows in batches of {EMBED_BATCH_SIZE}...")
    done = 0
    for i in range(0, len(pending), EMBED_BATCH_SIZE):
        batch = pending[i:i + EMBED_BATCH_SIZE]
        texts = [f"{n}. {b or ''}" for _, n, b in batch]

        if dry_run:
            print(f"    [dry-run] would embed batch {i // EMBED_BATCH_SIZE + 1}: "
                  f"{[r[1] for r in batch]}")
            done += len(batch)
            continue

        try:
            r = httpx.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": EMBED_MODEL, "input": texts},
                timeout=60.0,
            )
            r.raise_for_status()
            embedding_list = r.json()["data"]
        except Exception as exc:
            sys.stderr.write(f"  [embed] batch {i // EMBED_BATCH_SIZE + 1} failed: {exc}\n")
            # Retry one by one
            for pid, pname, pblurb in batch:
                try:
                    single_r = httpx.post(
                        OPENROUTER_URL,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={"model": EMBED_MODEL, "input": [f"{pname}. {pblurb or ''}"]},
                        timeout=60.0,
                    )
                    single_r.raise_for_status()
                    emb = str(single_r.json()["data"][0]["embedding"])
                    with conn.cursor() as cur2:
                        cur2.execute(
                            "UPDATE places SET embedding = %s WHERE id = %s",
                            (emb, pid),
                        )
                    done += 1
                    time.sleep(0.5)
                except Exception as exc2:
                    sys.stderr.write(f"  [embed] row {pid} ({pname}): {exc2}\n")
            continue

        with conn.cursor() as cur:
            for (pid, _, _), item in zip(batch, embedding_list):
                cur.execute(
                    "UPDATE places SET embedding = %s WHERE id = %s",
                    (str(item["embedding"]), pid),
                )
        conn.commit()
        done += len(batch)
        if (i // EMBED_BATCH_SIZE + 1) % 10 == 0:
            print(f"    ...{done}/{len(pending)} embedded", flush=True)
        time.sleep(1.0)

    return done


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Load OSM POIs into places table")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate + print plan, write nothing")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max rows to process (for testing)")
    parser.add_argument("--no-embed", action="store_true",
                        help="Skip embedding computation")
    parser.add_argument("--path", type=Path,
                        default=DEFAULT_CSV,
                        help="Input CSV path")
    args = parser.parse_args()

    csv_path = args.path
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    # 1. Parse CSV
    rows = read_csv(csv_path)
    if args.limit:
        rows = rows[: args.limit]
    print(f"[load_osm] parsed {len(rows)} rows from {csv_path}")

    # 2. Validate
    invalid: list[tuple[str, str, list[str]]] = []
    valid: list[dict] = []
    for raw in rows:
        problems = validate(raw)
        if problems:
            invalid.append((raw["name"], raw["source_url"], problems))
        else:
            valid.append(normalize(raw))

    if invalid:
        for name, url, problems in invalid:
            print(f"  INVALID {name} ({url}): {'; '.join(problems)}")

    print(f"[load_osm] valid={len(valid)}, invalid={len(invalid)}")

    if not valid:
        print("[load_osm] nothing to do")
        return

    # 3. Load existing places for deduplication
    print("[load_osm] checking for duplicates in DB...")
    with psycopg.connect(DSN) as conn:
        existing = load_existing_places(conn)
    print(f"[load_osm] {len(existing)} existing places loaded for dedup")

    skipped_dupes = 0
    to_insert: list[dict] = []
    for row in valid:
        if is_duplicate(row, existing):
            print(f"  SKIP (duplicate) {row['name']} @ ({row['lat']:.4f}, {row['lon']:.4f})")
            skipped_dupes += 1
        else:
            to_insert.append(row)

    print(f"[load_osm] to insert={len(to_insert)}, skipped-duplicates={skipped_dupes}")

    if not to_insert:
        print("[load_osm] nothing to insert")
        return

    if args.dry_run:
        for r in to_insert:
            print(f"  [dry-run] upsert: {r['name']} ({r['category']}, "
                  f"{r['district']}, {r['source_url']})")
        print("dry run — nothing written")
        return

    # 4. Upsert
    inserted = 0
    updated = 0
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            for r in to_insert:
                # Detect whether it exists
                cur.execute(
                    "SELECT 1 FROM places WHERE source_url = %s",
                    (r["source_url"],),
                )
                was_new = cur.fetchone() is None
                cur.execute(UPSERT_SQL, r)
                if was_new:
                    inserted += 1
                else:
                    updated += 1
        conn.commit()

    print(f"[load_osm] inserted={inserted}, updated={updated}")

    # 5. Embed missing rows
    embedded = 0
    if not args.no_embed:
        with psycopg.connect(DSN) as conn:
            embedded = embed_missing(conn, dry_run=args.dry_run)
        print(f"[load_osm] embedded={embedded}")

    print(f"\n[load_osm] SUMMARY: parsed={len(rows)}, invalid={len(invalid)}, "
          f"skipped-duplicates={skipped_dupes}, inserted={inserted}, "
          f"updated={updated}, embedded={embedded}")


if __name__ == "__main__":
    main()

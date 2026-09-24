"""Seed data/places_region.csv into the places table (Grodno voblast coverage).

Idempotent: upsert keyed by source_url, so re-runs update rather than duplicate.
Rows with a 'region:' source_url never touch scraper rows from planetabelarus.by.

Usage:
    python scripts/seed_region.py            # apply
    python scripts/seed_region.py --dry-run  # print the plan, change nothing
    OPENROUTER_API_KEY=... python scripts/seed_region.py --embed  # also embed
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

import psycopg

BACKEND = Path(__file__).resolve().parents[1]  # backend/scripts/*.py -> backend/
DATASETS = [
    (BACKEND / "data" / "places_grodno_city.csv", "city:"),
    (BACKEND / "data" / "places_region.csv", "region:"),
]
DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")

# Region bbox — mirrors settings.GRODNO_BBOX in agent/config.py and
# scripts/extract_grodno_pbf.sh. Seed refuses rows outside it.
BBOX = {"south": 52.75, "west": 23.35, "north": 54.80, "east": 30.75}

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


def read_rows(path: Path) -> list[dict]:
    """Parse the pipe-delimited region CSV (header may be commented out)."""
    lines = [
        ln for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    rows: list[dict] = []
    for raw in csv.reader(lines, delimiter="|"):
        if len(raw) != len(COLUMNS):
            raise SystemExit(f"expected {len(COLUMNS)} columns, got {len(raw)}: {raw[:3]}...")
        rows.append(dict(zip(COLUMNS, raw)))
    return rows


def validate(row: dict) -> list[str]:
    """Return a list of problems for this row (empty list = ok)."""
    problems: list[str] = []
    if row["category"] not in TAXONOMY:
        problems.append(f"category {row['category']!r} not in taxonomy")
    try:
        lat, lon = float(row["lat"]), float(row["lon"])
    except ValueError:
        return ["lat/lon not numeric"]
    if not (BBOX["south"] <= lat <= BBOX["north"]):
        problems.append(f"lat {lat} outside bbox")
    if not (BBOX["west"] <= lon <= BBOX["east"]):
        problems.append(f"lon {lon} outside bbox")
    try:
        json.loads(row["fun_facts"])
        json.loads(row["links"])
    except json.JSONDecodeError as e:
        problems.append(f"bad JSON ({e})")
    if not row["source_url"].startswith(("region:", "city:")):
        problems.append("source_url must be a 'region:'/'city:' key")
    if not isinstance(row["visit_minutes"], int):
        problems.append(f"visit_minutes {row['visit_minutes']!r} not an int")
    return problems


def normalize(row: dict) -> dict:
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


def embed_missing(cur) -> int:
    """Embed rows lacking vectors via OpenRouter (needs OPENROUTER_API_KEY)."""
    import httpx  # noqa: PLC0415 — optional path

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("  OPENROUTER_API_KEY not set — skipping embeddings")
        return 0

    cur.execute(
        "SELECT id, name, blurb FROM places "
        "WHERE (source_url LIKE 'region:%' OR source_url LIKE 'city:%') "
        "AND embedding IS NULL"
    )
    pending = cur.fetchall()
    if not pending:
        return 0

    url = "https://openrouter.ai/api/v1/embeddings"
    model = os.environ.get("OPENROUTER_EMBED_MODEL", "openai/text-embedding-3-small")
    done = 0
    for i in range(0, len(pending), 20):  # OpenRouter batch limit
        batch = pending[i:i + 20]
        texts = [f"{n}. {b or ''}" for _, n, b in batch]
        r = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "input": texts},
            timeout=30.0,
        )
        r.raise_for_status()
        for (pid, _, _), item in zip(batch, r.json()["data"]):
            cur.execute(
                "UPDATE places SET embedding = %s WHERE id = %s",
                (str(item["embedding"]), pid),
            )
        done += len(batch)
        time.sleep(1.0)
    return done


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    want_embed = "--embed" in sys.argv

    rows: list[dict] = []
    for path, prefix in DATASETS:
        dataset_rows = [normalize(r) for r in read_rows(path)]
        for r in dataset_rows:
            if not r["source_url"].startswith(prefix):
                raise SystemExit(
                    f"{path.name}: {r['source_url']!r} must use the '{prefix}' prefix"
                )
        rows.extend(dataset_rows)
        print(f"  {path.name}: {len(dataset_rows)} rows")

    invalid = [(r["name"], validate(r)) for r in rows]
    invalid = [(n, p) for n, p in invalid if p]
    if invalid:
        for name, problems in invalid:
            print(f"  INVALID {name}: {'; '.join(problems)}")
        raise SystemExit("dataset failed validation — fix data/*.csv")

    districts = {r["district"] for r in rows}
    print(f"{len(rows)} rows total, {len(districts)} districts")

    if dry_run:
        for r in rows:
            print(f"  [{r['source_url']}] {r['name']} ({r['town']}, {r['category']}, "
                  f"{r['visit_minutes']} мин)")
        print("dry run — nothing written")
        return

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(UPSERT_SQL, r)
            embedded = embed_missing(cur) if want_embed else 0
        conn.commit()

    print(f"upserted {len(rows)} rows" +
          (f", embedded {embedded}" if embedded else ""))


if __name__ == "__main__":
    main()

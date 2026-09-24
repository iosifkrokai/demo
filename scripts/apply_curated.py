"""One-shot: apply data/places_curated.csv (ground truth) to the places table.

The scraper (parse_places.py) owns name/description/lat/lon/photo_url; the
hand-curated CSV owns normalized_name/category/blurb/fun_fact and must be
applied AFTER it, otherwise scraped names like "Бывший Дом офицеров в Гродно"
win. See data/data_quality.md for why curation beats the local LLM here.

Usage:
    python scripts/apply_curated.py            # apply
    python scripts/apply_curated.py --dry-run   # print the plan, change nothing
"""

from __future__ import annotations

import csv
import os
import sys

import psycopg

DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5433/grodno")
CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "places_curated.csv",
)

UPDATE_SQL = """
    UPDATE places
       SET name = %(name)s,
           category = %(category)s,
           blurb = %(blurb)s,
           fun_fact = %(fun_fact)s,
           fun_facts = %(fun_facts)s,
           links = %(links)s
     WHERE id = %(id)s
"""


EXPECTED_HEADER = ["id", "normalized_name", "category", "blurb", "fun_fact", "fun_facts", "links"]


def read_curated(path: str) -> list[dict]:
    """Parses the pipe-delimited curated file.

    The header line is commented out in the repo (so the file stays valid CSV for
    other tooling), hence the explicit fieldnames; a real header is still accepted.
    """
    with open(path, encoding="utf-8") as fh:
        lines = [
            ln for ln in fh.read().splitlines() if ln.strip() and not ln.startswith("#")
        ]

    has_header = lines and not lines[0].split("|", 1)[0].strip().isdigit()
    if has_header and lines[0].split("|")[:4] != EXPECTED_HEADER[:4]:
        raise SystemExit(f"unexpected header {lines[0].split('|')}, expected {EXPECTED_HEADER}")

    reader = csv.DictReader(
        lines, fieldnames=None if has_header else EXPECTED_HEADER, delimiter="|"
    )

    return [
        {
            "id": int(row["id"]),
            "name": row["normalized_name"].strip(),
            "category": row["category"].strip() or None,
            "blurb": row["blurb"].strip() or None,
            "fun_fact": (row.get("fun_fact") or "").strip() or None,
            "fun_facts": (row.get("fun_facts") or "").strip() or None,
            "links": (row.get("links") or "").strip() or None,
        }
        for row in reader
    ]


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    rows = read_curated(CSV_PATH)
    print(f"{len(rows)} curated rows from {CSV_PATH}")

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, category, blurb, fun_fact, fun_facts, links FROM places")
            cols = [d.name for d in cur.description]
            existing = {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}

            missing = [r["id"] for r in rows if r["id"] not in existing]
            if missing:
                print(f"  warning: ids not in DB (run parse_places.py?): {missing}")

            without_fact = [r["id"] for r in rows if not r["fun_fact"]]
            if without_fact:
                print(f"  note: {len(without_fact)} row(s) have no fun_fact yet")

            changed = 0
            for row in rows:
                current = existing.get(row["id"])
                if not current:
                    continue
                if all(current[f] == row[f] for f in ("name", "category", "blurb", "fun_fact", "fun_facts", "links")):
                    continue
                changed += 1
                if dry_run:
                    print(f"  [{row['id']}] {row['name']}: "
                          f"cat {current['category']!r} -> {row['category']!r}, "
                          f"fact {'set' if row['fun_fact'] else 'empty'}")
                    continue
                cur.execute(UPDATE_SQL, row)

        conn.commit() if not dry_run else conn.rollback()

    print(f"{'would update' if dry_run else 'updated'} {changed} row(s)"
          + (" (dry run, nothing written)" if dry_run else ""))


if __name__ == "__main__":
    main()

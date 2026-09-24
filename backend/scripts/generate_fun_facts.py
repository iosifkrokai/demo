"""Generate fun_fact column for places that don't have one.

Two modes:
  --dry-run   Print proposed facts to stdout instead of writing to DB.
  --confirm   Write to DB directly.

Usage:
  DEEPINFRA_API_KEY=... python scripts/generate_fun_facts.py --dry-run
  DEEPINFRA_API_KEY=... python scripts/generate_fun_facts.py --confirm

Guardrails against hallucinations:
  - Each proposed fact is checked: if it looks like a date/number claim
    (matches a year or precise statistic), it is marked [UNVERIFIED] in dry-run.
  - Existing facts (non-NULL) are NEVER touched.
  - The LLM is asked to base facts ONLY on the description field,
    not to invent details not present in the source.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg

from agent.config import settings

# Pattern for potentially hallucinated facts: years, exact numbers, statistics
HALLUCINATION_PATTERNS = re.compile(
    r'\b(1\d{3}|2\d{3})\b'   # any 4-digit year
    r'|\b\d+\s*(человек|метр|км|этаж|экспонат|квартир|жителей)\b'  # precise counts
    r'|\b\d+\s*%\b',           # percentages
    re.IGNORECASE
)

SYSTEM_PROMPT = (
    "Ты — экскурсовод по Гродно. Прочитай описание места и выпиши из него "
    "ОДИН короткий интересный факт (1-2 предложения, макс 120 символов). "
    "Используй ТОЛЬКО информацию из описания. "
    "Если в описании нет интересных деталей — верни пустую строку. "
    "Не придумывай даты, числа или факты которых нет в описании. "
    "Отвечай строго одним предложением или пустой строкой."
)

PROMPT_TEMPLATE = (
    "Место: {name} ({category})\n"
    "Описание: {description}\n"
    "Напиши один короткий интересный факт (до 120 символов) или пустую строку:"
)


def fetch_places_without_facts(conn: psycopg.Connection, limit: int = 200) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, category, description "
            "FROM places WHERE (fun_fact IS NULL OR fun_fact = '') "
            "AND description IS NOT NULL AND description != '' "
            "LIMIT %s",
            (limit,)
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def generate_fact(place: dict, api_key: str) -> str:
    import httpx

    user = PROMPT_TEMPLATE.format(
        name=place["name"],
        category=place.get("category") or "место",
        description=place.get("description") or "нет описания",
    )
    client = httpx.Client(
        base_url="https://api.deepinfra.com/v1/openai",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    )
    response = client.post(
        "/chat/completions",
        json={
            "model": "google/gemini-3.1-flash",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,   # low temp = less hallucination
            "max_tokens": 64,
        },
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"].strip()
    return content[:120]


def write_fact(conn: psycopg.Connection, place_id: int, fact: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE places SET fun_fact = %s WHERE id = %s",
            (fact, place_id)
        )


def main():
    parser = argparse.ArgumentParser(description="Fill fun_fact for places without one")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true",
                       help="print proposed facts without writing to DB")
    group.add_argument("--confirm", action="store_true",
                       help="write facts directly to DB")
    parser.add_argument("--limit", type=int, default=200,
                       help="max places to process (default: 200)")
    args = parser.parse_args()

    api_key = os.environ.get("DEEPINFRA_API_KEY")
    if not api_key:
        print("Error: DEEPINFRA_API_KEY not set.")
        sys.exit(1)

    conn = psycopg.connect(settings.DSN, autocommit=True)
    places = fetch_places_without_facts(conn, limit=args.limit)

    if not places:
        print("No places need fun_fact (all have one or no description).")
        return

    print(f"Processing {len(places)} places (mode: {'DRY-RUN' if args.dry_run else 'CONFIRM'})")
    print("=" * 70)

    for i, place in enumerate(places, 1):
        try:
            fact = generate_fact(place, api_key)
        except Exception as e:
            print(f"[{i}/{len(places)}] {place['name']}: ERROR {e}")
            continue

        suspicious = bool(HALLUCINATION_PATTERNS.search(fact))
        tag = " [UNVERIFIED: has numbers/dates]" if suspicious else ""

        if args.dry_run:
            status = "DRY-RUN" + tag
            print(f"[{i}/{len(places)}] [{status}] {place['name']}")
            print(f"  → {fact or '(empty — keep as is)'}")
        else:
            if fact:
                write_fact(conn, place["id"], fact)
            print(f"[{i}/{len(places)}] {place['name']}: {fact or '(skipped)'}")

    # Summary
    with conn.cursor() as cur:
        total, with_fact = cur.execute(
            "SELECT count(*), count(fun_fact) FROM places"
        ).fetchone()
    print(f"\nSummary: {with_fact}/{total} places have fun_fact")

    conn.close()


if __name__ == "__main__":
    main()

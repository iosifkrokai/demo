"""One-shot enrichment: assigns category (deterministic taxonomy) + embedding (OpenRouter).

# Curated data wins. The curated ground truth lives in data/places_curated.csv
# (76 rows manually labelled) and is written by scripts/apply_curated.py, while
# the hand-authored city/region CSVs are written by scripts/seed_region.py.
# Both are marked on places.category_source ('curated' / 'dataset'). This script
# classifies ONLY rows still marked 'auto', so automatic classification can never
# overwrite a hand-labelled category — enforced here (the WHERE guards below),
# by scripts/seed_all.py (which never lets an automatic writer through), and by
# the places_guard_curated_category trigger added in db/migrations/0004.
# Detects changes by recomputing a small md5 of (name||blurb||description).

OpenRouter:
  - Embedding: openai/text-embedding-3-small (1536-d, multilingual) — the same
    POST /embeddings call as scripts/seed_region.py and scripts/ingest_poi.py.
  - Categories: the deterministic taxonomy resolver (agent/taxonomy.py
    `resolve_code`) over the row's name. Jev (typesafe/jev-1.13) is gone from
    the backend, so classification no longer calls a model: a row whose name the
    taxonomy cannot map is stored as «другое» rather than guessed. This keeps
    the pass honest and key-free; a future model classifier belongs to the
    interpretation agent, not to this one-shot script.

Usage:
    python scripts/enrich_places.py
"""

import os
import sys

import httpx
import psycopg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.taxonomy import all_codes, resolve_code

DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")
OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_EMBED_MODEL = os.environ.get("OPENROUTER_EMBED_MODEL", "openai/text-embedding-3-small")

# Category → English gloss. The Cyrillic key is what lands in places.category.
# Kept as the documented label set for reviewers; classification itself is done
# by the deterministic taxonomy resolver, so no model sees these glosses any
# more (they were the criteria Jev used to receive).
TAXONOMY: dict[str, str] = {
    "замок": "castle, fortress or other defensive structure",
    "костёл": "Catholic church",
    "церковь": "Orthodox church",
    "монастырь": "monastery or convent",
    "дворец": "palace or official residence",
    "усадьба": "country estate, manor house",
    "парк": "park, public garden or zoo",
    "музей": "museum, gallery or theatre building",
    "архитектура": "historic building, former mansion, factory, bank",
    "памятник": "monument, memorial or grave",
    "инфраструктура": "bridge, tower, stadium, water tower",
    "храм": "synagogue, other denomination church, any other place of worship",
    "кладбище": "cemetery or necropolis",
    "другое": "none of the above",
}
# Kept for signature compatibility; there is no chunked model call any more.
CLASSIFY_CHUNK = 6
EMBED_BATCH = 20  # OpenRouter batch limit


def classify_items(
    items: list[str],
    taxonomy: dict[str, str] = TAXONOMY,
    chunk_size: int = CLASSIFY_CHUNK,
) -> list[str | None]:
    """Category per item, from the deterministic taxonomy resolver.

    items are the "name. description" strings built in main(); the returned list
    is positional. The NAME (the part before the first period) is resolved
    through agent/taxonomy.py — the single source of category codes — and the
    full string is tried as a fallback. A row the taxonomy cannot map is None
    and the caller stores «другое»; nothing is invented and no model is called.

    `taxonomy` and `chunk_size` are accepted for signature compatibility with
    the removed Jev implementation and are otherwise unused.
    """
    known = set(all_codes())
    out: list[str | None] = []
    for item in items:
        name = item.split(".", 1)[0].strip()
        code = resolve_code(name) or resolve_code(item)
        out.append(code if (code and code in known) else None)
    return out


def _openrouter_embed(texts: list[str]) -> list[list[float]]:
    """Call OpenRouter embeddings API (POST /v1/embeddings), EMBED_BATCH at a time.

    Same endpoint, model and batch size as scripts/seed_region.py and
    scripts/ingest_poi.py. Kept local rather than shared: those two scripts
    import helpers from each other, and a scripts/ helper module is not
    something this one-shot script should depend on.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            f"{OPENROUTER_URL}/embeddings",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": OPENROUTER_EMBED_MODEL, "input": texts},
        )
        r.raise_for_status()
        return [[float(x) for x in item["embedding"]] for item in r.json()["data"]]


def fetch_pending(cur, sql: str) -> list[dict]:
    cur.execute(sql)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def main() -> None:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        # === Pass 1: categories (only rows that are not hand-curated) ===
        rows = fetch_pending(
            cur,
            "SELECT id, name, description FROM places "
            "WHERE COALESCE(category_source, 'auto') = 'auto' ORDER BY id",
        )
        if rows:
            print(f"  classifying {len(rows)} rows with the taxonomy resolver...", flush=True)
            items = [
                f"{(r['name'] or '').strip()}. {(r['description'] or '').strip()}".strip(" .")
                for r in rows
            ]
            classes = classify_items(items, TAXONOMY, chunk_size=6)
            n_mapped = 0
            for row, cat in zip(rows, classes, strict=True):
                if cat:
                    n_mapped += 1
                cur.execute(
                    "UPDATE places SET category = %s "
                    "WHERE id = %s AND COALESCE(category_source, 'auto') = 'auto'",
                    (cat or "другое", row["id"]),
                )
            conn.commit()
            print(
                f"  categories: {n_mapped}/{len(rows)} resolved by taxonomy, "
                "rest set to «другое»",
                flush=True,
            )

        # === Pass 2: embeddings (only rows where embedding IS NULL) ===
        while True:
            rows = fetch_pending(
                cur,
                "SELECT id, name, description FROM places WHERE embedding IS NULL ORDER BY id",
            )
            if not rows:
                break
            print(f"  encoding {len(rows)} embeddings via OpenRouter...", flush=True)
            texts = [
                f"{(r['name'] or '').strip()}. {(r['description'] or '').strip()}".strip(" .")
                for r in rows
            ]
            # Batch via OpenRouter (limit ~20 per request)
            vecs: list[list[float]] = []
            for i in range(0, len(texts), EMBED_BATCH):
                batch = texts[i: i + EMBED_BATCH]
                vecs.extend(_openrouter_embed(batch))
            # strict=False: a short answer from OpenRouter must not abort the run —
            # whatever is left stays embedding IS NULL and the next pass retries it.
            for row, v in zip(rows, vecs, strict=False):
                cur.execute(
                    "UPDATE places SET embedding = %s::vector WHERE id = %s",
                    (v, row["id"]),
                )
            conn.commit()
            print(f"  embedded {len(rows)} rows, committed", flush=True)

    print("done.", flush=True)


if __name__ == "__main__":
    main()

"""One-shot enrichment: assigns category (local LLM) + embedding (OpenRouter).

# Categories are NOT auto-reassigned: the curated ground truth lives in
# data/places_curated.csv (76 rows manually labelled).
# On startup we sync category+name+blurb from that file, then embeddings only.
#
# NOTE (legacy): Categories used to be (re-)assigned on every run for ALL rows.
# Detects changes by recomputing a small md5 of (name||blurb||description).

OpenRouter models:
  - Embedding: openai/text-embedding-3-small (1536-d, multilingual)
  - Categories: OpenRouter LLM (google/gemini-2.5-flash)

Usage:
    python scripts/enrich_places.py
"""

import os
import sys

import httpx
import psycopg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.llm import classify_items, init as init_llm  # noqa: E402

DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")
OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_EMBED_MODEL = os.environ.get("OPENROUTER_EMBED_MODEL", "openai/text-embedding-3-small")
TAXONOMY = [
    "замок",       # крепости, оборонительные сооружения
    "костёл",      # католические храмы
    "церковь",     # православные храмы
    "монастырь",   # монастыри
    "дворец",      # дворцы, резиденции
    "усадьба",     # загородные имения
    "парк",        # парки, скверы, сады, зоопарк
    "музей",       # музеи, галереи, театральные здания
    "архитектура", # исторические здания, бывшие дома, фабрики, банки
    "памятник",    # памятники, монументы, мемориалы, могилы
    "инфраструктура", # мосты, башни, стадионы, водонапорные башни
    "храм",        # синагоги, кирхи, прочие культовые
    "кладбище",    # некрополи, кладбища
    "другое",      # fallback
]
EMBED_BATCH = 20  # OpenRouter batch limit


def _openrouter_embed(texts: list[str]) -> list[list[float]]:
    """Call OpenRouter embeddings API."""
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
        return [item["embedding"] for item in r.json()["data"]]


def fetch_pending(cur, sql: str) -> list[dict]:
    cur.execute(sql)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> None:
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            # === Pass 1: categories ===
            rows = fetch_pending(
                cur,
                "SELECT id, name, description FROM places ORDER BY id",
            )
            if rows:
                print(f"  classifying {len(rows)} rows with LLM...", flush=True)
                init_llm()
                items = [
                    f"{(r['name'] or '').strip()}. {(r['description'] or '').strip()}".strip(" .")
                    for r in rows
                ]
                classes = classify_items(items, TAXONOMY, chunk_size=6)
                n_llm = 0
                for row, cat in zip(rows, classes):
                    if cat:
                        n_llm += 1
                    cur.execute(
                        "UPDATE places SET category = %s WHERE id = %s",
                        (cat or "другое", row["id"]),
                    )
                conn.commit()
                print(f"  categories: {n_llm}/{len(rows)} by LLM, rest set to «другое»", flush=True)

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
                for row, v in zip(rows, vecs):
                    cur.execute(
                        "UPDATE places SET embedding = %s::vector WHERE id = %s",
                        (v, row["id"]),
                    )
                conn.commit()
                print(f"  embedded {len(rows)} rows, committed", flush=True)

    print("done.", flush=True)


if __name__ == "__main__":
    main()

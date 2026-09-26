"""One-shot enrichment: assigns category (Jev typed decisions) + embedding (OpenRouter).

# Categories are NOT auto-reassigned: the curated ground truth lives in
# data/places_curated.csv (76 rows manually labelled) and is written by
# scripts/apply_curated.py (name/category/blurb/fun_fact). This script fills in
# what the CSV does not cover: the category guess and the embedding vector.
#
# NOTE (legacy): Categories used to be (re-)assigned on every run for ALL rows.
# Detects changes by recomputing a small md5 of (name||blurb||description).

OpenRouter:
  - Embedding: openai/text-embedding-3-small (1536-d, multilingual) — the same
    POST /embeddings call as scripts/seed_region.py and scripts/ingest_poi.py.
  - Categories: TypeSafe Jev (typesafe/jev-1.13) typed `choice` questions via
    agent/jev.py. This used to be agent/llm.py + a Gemini chat completion; that
    module was removed and Jev is the project's decision model now
    (agent/constants.py::JEV_MODEL, same pattern as agent/planner/intent.py).

Usage:
    python scripts/enrich_places.py
"""

import os
import sys

import httpx
import psycopg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent import jev

DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")
OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_EMBED_MODEL = os.environ.get("OPENROUTER_EMBED_MODEL", "openai/text-embedding-3-small")

# Category → English gloss. The Cyrillic key is what lands in places.category;
# the gloss is the description Jev gets for that choice criterion (its training
# language is English — same reasoning as agent/planner/intent.py).
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
# Items per /systemone call. Their "parallel questions" pattern: one choice
# question per item inside a single request, not one call per item.
CLASSIFY_CHUNK = 6
EMBED_BATCH = 20  # OpenRouter batch limit


def classify_items(
    items: list[str],
    taxonomy: dict[str, str] = TAXONOMY,
    chunk_size: int = CLASSIFY_CHUNK,
) -> list[str | None]:
    """Category per item, from typed Jev `choice` questions.

    items are the "name. description" strings built in main(); the returned list
    is positional. Each chunk of `chunk_size` items becomes ONE /systemone call
    carrying one choice question per item.

    A missing answer is None and the caller stores «другое». A value outside the
    taxonomy raises — same "fail loud on drift" rule as agent/planner/intent.py,
    because a stray label here would overwrite a hand-curated category.
    """
    out: list[str | None] = []
    for start in range(0, len(items), chunk_size):
        chunk = items[start: start + chunk_size]
        questions = {
            f"item_{i}": {
                "type": "choice",
                "instructions": (
                    "Which category does this tourist place belong to? The state "
                    "is a list of places, each with an `id`, a name and a short "
                    "description in `text`."
                ),
                "criteria": dict(taxonomy),
            }
            for i in range(len(chunk))
        }
        answers = jev.ask([{"id": i, "text": t} for i, t in enumerate(chunk)], questions)
        for i in range(len(chunk)):
            answer = answers.get(f"item_{i}")
            if not answer:
                out.append(None)
                continue
            cat = jev.choice(answer)
            if cat not in taxonomy:
                raise ValueError(f"jev: unknown category {cat!r} — add it to TAXONOMY")
            out.append(cat)
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
        return [item["embedding"] for item in r.json()["data"]]


def fetch_pending(cur, sql: str) -> list[dict]:
    cur.execute(sql)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def main() -> None:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        # === Pass 1: categories ===
        rows = fetch_pending(
            cur,
            "SELECT id, name, description FROM places ORDER BY id",
        )
        if rows:
            print(f"  classifying {len(rows)} rows with Jev...", flush=True)
            items = [
                f"{(r['name'] or '').strip()}. {(r['description'] or '').strip()}".strip(" .")
                for r in rows
            ]
            classes = classify_items(items, TAXONOMY, chunk_size=6)
            n_llm = 0
            for row, cat in zip(rows, classes, strict=True):
                if cat:
                    n_llm += 1
                cur.execute(
                    "UPDATE places SET category = %s WHERE id = %s",
                    (cat or "другое", row["id"]),
                )
            conn.commit()
            print(f"  categories: {n_llm}/{len(rows)} by Jev, rest set to «другое»", flush=True)

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

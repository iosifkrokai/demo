"""One-shot enrichment: assigns category (local LLM) + embedding (local fastembed).

Idempotent: skips rows that already have embeddings; categories are attempted
on each pass and may stay NULL if the local LLM returns unparsable output.

Local models only — no API keys needed:
  - Embedding: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384-d)
  - Categories: Qwen2.5-1.5B-Instruct q4_k_m GGUF via llama-cpp-python
    (reuses agent/llm.py; the model is downloaded once when the agent first
    starts and cached in ~/.cache/huggingface/)

Usage:
    python scripts/enrich_places.py
"""

import json
import os

import psycopg
from fastembed import TextEmbedding

DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")

# Fixed taxonomy.
TAXONOMY = [
    "замок",   # замки, крепости, фортификации
    "храм",    # костёлы, церкви, монастыри, синагоги
    "дворец",  # дворцы, усадьбы
    "музей",   # музеи, галереи, театры
    "парк",    # парки, скверы, сады
    "другое",
]

# Keyword-based category detection. Replaces the previous LLM-based call,
# which Qwen 1.5B couldn't reliably produce JSON for at scale. The agent
# doesn't read this column, so we don't need the precision.
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "замок":  ["замок", "крепость", "форт", "бастион", "цитадел", "замков"],
    "храм":   ["костёл", "костел", "церковь", "монастыр", "синагог", "собор",
               "храм", "мечеть", "часовня", "молельн", "приход"],
    "дворец": ["дворец", "усадьба", "палац", "резиденц", "двор"],
    "музей":  ["музей", "галерея", "театр", "экспозиц", "аптека"],
    "парк":   ["парк", "сквер", "сад", "ботанич", "заповедник"],
}


def classify_by_keywords(name: str, description: str) -> str:
    text = f"{name or ''} {description or ''}".lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return cat
    return "другое"

EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # 384-d, multilingual, ONNX via fastembed
EMBED_BATCH = 64


def fetch_pending(cur, sql: str) -> list[dict]:
    cur.execute(sql)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> None:
    print(f"loading {EMBED_MODEL} via fastembed (~470 MB cached in ~/.cache/fastembed)...", flush=True)
    model = TextEmbedding(EMBED_MODEL)
    dim = len(next(iter(model.embed(["warmup"]))))
    assert dim == 384, f"expected 384-dim, got {dim}"
    print(f"embedding model loaded, dim={dim}", flush=True)

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            # === Pass 1: categories (keyword-based, instant) ===
            rows = fetch_pending(
                cur,
                "SELECT id, name, description FROM places WHERE category IS NULL ORDER BY id",
            )
            if rows:
                print(f"  categorizing {len(rows)} rows (keyword matcher)...", flush=True)
                for row in rows:
                    cat = classify_by_keywords(row.get("name") or "", row.get("description") or "")
                    cur.execute(
                        "UPDATE places SET category = %s WHERE id = %s",
                        (cat, row["id"]),
                    )
                conn.commit()
                print(f"  categories: {len(rows)}/{len(rows)} assigned", flush=True)

            # === Pass 2: embeddings (only rows where embedding IS NULL) ===
            while True:
                rows = fetch_pending(
                    cur,
                    "SELECT id, name, description FROM places WHERE embedding IS NULL ORDER BY id",
                )
                if not rows:
                    break
                print(f"  encoding {len(rows)} embeddings (CPU)...", flush=True)
                texts = [
                    f"{(r['name'] or '').strip()}. {(r['description'] or '').strip()}".strip(" .")
                    for r in rows
                ]
                vecs = list(
                    model.embed(texts, batch_size=EMBED_BATCH, show_progress=True)
                )
                for row, v in zip(rows, vecs):
                    cur.execute(
                        "UPDATE places SET embedding = %s::vector WHERE id = %s",
                        (v.tolist() if hasattr(v, "tolist") else list(v), row["id"]),
                    )
                conn.commit()
                print(f"  embedded {len(rows)} rows, committed", flush=True)

    print("done.", flush=True)


if __name__ == "__main__":
    main()

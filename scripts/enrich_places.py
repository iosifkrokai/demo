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
import sys

import psycopg
from fastembed import TextEmbedding

# Make agent.llm importable when this script is run from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agent.llm import classify_items, init as init_llm  # noqa: E402

DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")

# Fixed taxonomy. Kept short on purpose: Qwen2.5-1.5B at q4_k_m struggles with
# long label lists in structured-output prompts (often returns invalid JSON
# for more than ~6 options). 6 broad buckets give reasonable reliability on
# CPU. The agent doesn't read this column — vector similarity on embeddings
# does the actual route selection — so partial / NULL categories are fine.
TAXONOMY = [
    "замок",   # замки, крепости, фортификации
    "храм",    # костёлы, церкви, монастыри, синагоги
    "дворец",  # дворцы, усадьбы
    "музей",   # музеи, галереи, театры
    "парк",    # парки, скверы, сады
    "другое",
]

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

    print("loading local LLM (Qwen2.5-1.5B) for categories...", flush=True)
    init_llm()  # reuse the agent's loaded model if already in this process; otherwise load

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            # === Pass 1: categories (single pass, best-effort) ===
            # Qwen 1.5B at q4_k_m on CPU produces invalid JSON on a meaningful
            # share of chunks, so the categories loop runs ONCE and we accept
            # whatever got assigned. NULL rows stay NULL — the agent doesn't
            # read this column.
            rows = fetch_pending(
                cur,
                "SELECT id, name, description FROM places WHERE category IS NULL ORDER BY id",
            )
            if rows:
                print(f"  categorizing {len(rows)} rows (chunks of 12 via local LLM)...", flush=True)
                items = [
                    f"{(r.get('name') or '').strip()}. {(r.get('description') or '')[:200]}".strip(" .")
                    for r in rows
                ]
                cats = classify_items(items, TAXONOMY)
                for row, cat in zip(rows, cats):
                    if cat is not None:
                        cur.execute(
                            "UPDATE places SET category = %s WHERE id = %s",
                            (cat, row["id"]),
                        )
                conn.commit()
                assigned = sum(c is not None for c in cats)
                print(
                    f"  categories: {assigned}/{len(rows)} assigned (rest NULL — "
                    "Qwen 1.5B is best-effort, agent doesn't use this column)",
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

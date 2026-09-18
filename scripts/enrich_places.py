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

# Fixed taxonomy.
TAXONOMY = [
    "замок", "костёл", "церковь", "монастырь",
    "дворец", "усадьба", "парк",
    "музей", "памятник", "городище", "другое",
]

EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # 384-d, multilingual, ONNX via fastembed
EMBED_BATCH = 64


def fetch_pending(cur, limit: int | None = None) -> list[dict]:
    # Only gate on embedding being missing — categories are attempted on each
    # pass and the loop exits as soon as embeddings are populated, even if a
    # few categories come back as NULL.
    sql = "SELECT id, name, description FROM places WHERE embedding IS NULL ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
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
            while True:
                rows = fetch_pending(cur)
                if not rows:
                    break
                print(f"processing {len(rows)} rows...", flush=True)

                # 1) categories via local LLM, batched in chunks of 12
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
                print(f"  categories: {sum(c is not None for c in cats)}/{len(rows)} assigned", flush=True)

                # 2) embeddings via local model. show_progress=True prints a tqdm
                # bar so the user sees the script is alive (CPU encode of 384-d
                # vectors takes ~1-2 min for ~76 rows).
                texts = [
                    f"{(r['name'] or '').strip()}. {(r['description'] or '').strip()}".strip(" .")
                    for r in rows
                ]
                print(f"  encoding {len(texts)} embeddings (CPU)...", flush=True)
                vecs = list(
                    model.embed(texts, batch_size=EMBED_BATCH, show_progress=True)
                )
                for row, v in zip(rows, vecs):
                    # v is a numpy ndarray; tolist() gives a plain Python list
                    # that psycopg3 can adapt into vector(384) via %s::vector.
                    cur.execute(
                        "UPDATE places SET embedding = %s::vector WHERE id = %s",
                        (v.tolist() if hasattr(v, "tolist") else list(v), row["id"]),
                    )
                conn.commit()
                print(f"  embedded {len(rows)} rows, committed", flush=True)

    print("done.", flush=True)


if __name__ == "__main__":
    main()

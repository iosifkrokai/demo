"""One-shot enrichment: assigns category (local LLM) + embedding (local fastembed).

# Categories are NOT auto-reassigned: the curated ground truth lives in
# data/places_curated.csv (76 rows manually labelled, see agent/data_quality.md).
# On startup we sync category+name+blurb from that file, then embeddings only.
# To re-classify, run scripts/reclassify_with_llm.py explicitly.
#
# NOTE (legacy): Categories used to be (re-)assigned on every run for ALL rows: the local LLM classifies
name+description into the taxonomy below (grammar-constrained JSON output, so
only taxonomy strings can come back); anything the model fails on stays
«другое». Embeddings are recomputed for rows whose name/blurb/description has changed,
# then for any remaining rows where embedding IS NULL.
# Detects changes by recomputing a small md5 of (name||blurb||description).

Local models only — no API keys needed:
  - Embedding: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384-d)
  - Categories: Qwen2.5-1.5B-Instruct q4_k_m GGUF via llama-cpp-python
    (reuses agent/llm.py; the model is downloaded once when the agent first
    starts and cached in ~/.cache/huggingface/)

Usage:
    python scripts/enrich_places.py
"""

import os
import sys

import psycopg
from fastembed import TextEmbedding

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.llm import classify_items, init as init_llm  # noqa: E402

DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5433/grodno")

# Taxonomy aligned with data/places_curated.csv (manually curated ground truth).
# Includes original categories from agent/llm.py plus finer-grained ones that
# Qwen 1.5B could not reliably distinguish (архитектура, кладбище, инфраструктура).
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

# Keyword matcher removed: substring rules misclassified names mentioning
# «Монастырский» (surname), «напоминает синагогу» (simile), etc. Rows the LLM
# couldn't classify reliably just stay «другое».

EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # 384-d, multilingual, ONNX via fastembed
EMBED_BATCH = 64


def fetch_pending(cur, sql: str) -> list[dict]:
    cur.execute(sql)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> None:
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            # === Pass 1: categories — ALL rows, every run ===
            rows = fetch_pending(
                cur,
                "SELECT id, name, description FROM places ORDER BY id",
            )
            if rows:
                print(f"  classifying {len(rows)} rows with the local LLM...", flush=True)
                init_llm()
                items = [
                    f"{(r['name'] or '').strip()}. {(r['description'] or '').strip()}".strip(" .")
                    for r in rows
                ]
                # Smaller chunks than the default: name+description rows are
                # long, and the model's n_ctx is only 2048.
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
            model = None
            while True:
                rows = fetch_pending(
                    cur,
                    "SELECT id, name, description FROM places WHERE embedding IS NULL ORDER BY id",
                )
                if not rows:
                    break
                if model is None:
                    print(f"loading {EMBED_MODEL} via fastembed (~470 MB cached in ~/.cache/fastembed)...", flush=True)
                    model = TextEmbedding(EMBED_MODEL)
                    dim = len(next(iter(model.embed(["warmup"]))))
                    assert dim == 384, f"expected 384-dim, got {dim}"
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

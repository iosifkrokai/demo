"""One-shot enrichment: assigns category (LLM) + embedding (local fastembed).

Idempotent: skips rows that already have both fields. Re-runnable after partial failures.

Reads OPENAI_API_KEY from env to enable LLM-based category assignment. If unset, category is
left NULL (embedding still runs). The agent's query pipeline tolerates NULL categories.

Embedding model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384-d, ONNX via
fastembed, ~470 MB on first load, cached in ~/.cache/fastembed/). No torch, no CUDA.

Usage:
    OPENAI_API_KEY=sk-... python scripts/enrich_places.py
"""

import json
import os

import psycopg
from fastembed import TextEmbedding
from openai import OpenAI

DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")

# Fixed taxonomy. Pass to the LLM as the only allowed values.
TAXONOMY = [
    "замок", "костёл", "церковь", "монастырь",
    "дворец", "усадьба", "парк",
    "музей", "памятник", "городище", "другое",
]
TAXONOMY_STR = " | ".join(TAXONOMY)

EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # 384-d, multilingual, ONNX via fastembed
EMBED_BATCH = 64

CATEGORY_PROMPT = (
    "Ты — ассистент, который классифицирует достопримечательности Гродненской области.\n"
    "Для каждого объекта выбери ровно одну категорию из списка ниже. "
    "Вход приходит как JSON-объект со списком объектов {id, name, desc} под ключом 'items'.\n"
    "Верни JSON-объект вида {\"categories\": [\"категория1\", \"категория2\", ...]} той же длины. "
    "Если сомневаешься — ставь 'другое'. Никаких пояснений, только JSON.\n\n"
    f"Категории: {TAXONOMY_STR}"
)


def fetch_pending(cur, limit: int | None = None) -> list[dict]:
    sql = (
        "SELECT id, name, description FROM places "
        "WHERE embedding IS NULL OR category IS NULL "
        "ORDER BY id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    cur.execute(sql)
    return [dict(r) for r in cur.fetchall()]


def assign_categories(rows: list[dict]) -> list[str | None]:
    """One LLM call for the whole batch. Returns parallel list of categories."""
    if not os.environ.get("OPENAI_API_KEY"):
        return [None] * len(rows)
    client = OpenAI()
    payload = {"items": [{"id": r["id"], "name": r["name"], "desc": (r.get("description") or "")[:500]} for r in rows]}
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": CATEGORY_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
    )
    try:
        parsed = json.loads(resp.choices[0].message.content)
    except Exception:
        return [None] * len(rows)
    cats = parsed.get("categories") if isinstance(parsed, dict) else None
    if not isinstance(cats, list) or len(cats) != len(rows):
        return [None] * len(rows)
    out: list[str | None] = []
    for c in cats:
        if isinstance(c, str) and c in TAXONOMY:
            out.append(c)
        else:
            out.append(None)
    return out


def main() -> None:
    print(f"loading {EMBED_MODEL} via fastembed (one-time, ~120 MB cached in ~/.cache/fastembed)...")
    model = TextEmbedding(EMBED_MODEL)
    dim = len(next(iter(model.embed(["test"]))))
    assert dim == 384, f"expected 384-dim, got {dim}"

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            while True:
                rows = fetch_pending(cur)
                if not rows:
                    break
                print(f"processing {len(rows)} rows...")

                # 1) categories via LLM (one batched call)
                cats = assign_categories(rows)
                for row, cat in zip(rows, cats):
                    if cat is not None:
                        cur.execute(
                            "UPDATE places SET category = %s WHERE id = %s",
                            (cat, row["id"]),
                        )

                # 2) embeddings via local model
                texts = [
                    f"{(r['name'] or '').strip()}. {(r['description'] or '').strip()}".strip(" .")
                    for r in rows
                ]
                vecs = list(model.embed(texts, batch_size=EMBED_BATCH))
                for row, v in zip(rows, vecs):
                    # v is a numpy ndarray; tolist() gives a plain Python list
                    # that psycopg3 can adapt into vector(384) via %s::vector.
                    cur.execute(
                        "UPDATE places SET embedding = %s::vector WHERE id = %s",
                        (v.tolist() if hasattr(v, "tolist") else list(v), row["id"]),
                    )
                conn.commit()

    print("done.")


if __name__ == "__main__":
    main()

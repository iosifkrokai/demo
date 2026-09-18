"""One-shot enrichment: assigns category (LLM) + embedding (local sentence-transformer).

Idempotent: skips rows that already have both fields. Re-runnable after partial failures.

Reads OPENAI_API_KEY from env to enable LLM-based category assignment. If unset, category is
left NULL (embedding still runs). The agent's query pipeline tolerates NULL categories.

Usage:
    OPENAI_API_KEY=sk-... python scripts/enrich_places.py
"""

import os
from typing import Iterable

import psycopg
from openai import OpenAI
from sentence_transformers import SentenceTransformer

DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")

# Fixed taxonomy. Pass to the LLM as the only allowed values.
TAXONOMY = [
    "замок", "костёл", "церковь", "монастырь",
    "дворец", "усадьба", "парк",
    "музей", "памятник", "городище", "другое",
]
TAXONOMY_STR = " | ".join(TAXONOMY)

CATEGORY_PROMPT = (
    "Ты — ассистент, который классифицирует достопримечательности Гродненской области.\n"
    "Для каждого объекта выбери ровно одну категорию из списка ниже. "
    "Вход приходит как JSON-объект со списком объектов {id, name, desc} под ключом 'items'.\n"
    "Верни JSON-объект вида {\"categories\": [\"категория1\", \"категория2\", ...]} той же длины. "
    "Если сомневаешься — ставь 'другое'. Никаких пояснений, только JSON.\n\n"
    f"Категории: {TAXONOMY_STR}"
)

EMBED_BATCH = 64


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
    print("loading sentence-transformers model (one-time)...")
    model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    dim = model.get_sentence_embedding_dimension()
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
                vecs = model.encode(
                    texts,
                    batch_size=EMBED_BATCH,
                    show_progress_bar=False,
                    normalize_embeddings=True,
                )
                for row, v in zip(rows, vecs):
                    cur.execute(
                        "UPDATE places SET embedding = %s::vector WHERE id = %s",
                        (v.tolist(), row["id"]),
                    )
                conn.commit()

    print("done.")


if __name__ == "__main__":
    main()

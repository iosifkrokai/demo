"""LLM wrapper for query parsing.

If OPENAI_API_KEY is set: ask gpt-4o-mini to extract {keywords, categories, n_points, region_bbox}.
Otherwise: regex-based keyword + category extraction with a tiny synonym table.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any


CATEGORY_SYNONYMS: dict[str, list[str]] = {
    "замок": ["замок", "крепость", "castle"],
    "костёл": ["костёл", "костел"],
    "церковь": ["церковь", "church"],
    "монастырь": ["монастырь"],
    "дворец": ["дворец", "palace"],
    "усадьба": ["усадьба", "manor"],
    "парк": ["парк", "сквер", "park", "сад"],
    "музей": ["музей", "museum"],
    "памятник": ["памятник", "монумент", "monument"],
    "городище": ["городище", "hillfort"],
}


def _fallback_parse(query: str, default_n_points: int) -> dict[str, Any]:
    q = query.lower()
    categories = [cat for cat, syns in CATEGORY_SYNONYMS.items() if any(s in q for s in syns)]
    keywords = [w for w in re.findall(r"[а-яёa-z]{3,}", q)]
    # crude n_points: "пара" -> 2, "три" -> 3, "четыре" -> 4, etc., else default.
    word_to_n = {"пара": 2, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7}
    n_points = next((v for k, v in word_to_n.items() if re.search(rf"\b{k}\b", q)), default_n_points)
    return {"keywords": keywords, "categories": categories, "n_points": n_points, "region_bbox": None}


def parse_query(query: str, default_n_points: int = 4) -> dict[str, Any]:
    if not os.environ.get("OPENAI_API_KEY"):
        return _fallback_parse(query, default_n_points)

    from openai import OpenAI  # imported lazily so the agent runs without the key

    client = OpenAI()
    sys_prompt = (
        "Extract structured intent from a Russian-language walking-tour query about Grodno.\n"
        "Return JSON with keys: keywords (list of strings), categories "
        "(list of: замок|костёл|церковь|монастырь|дворец|усадьба|парк|музей|памятник|городище), "
        "n_points (int 2..8), region_bbox ([south,west,north,east] or null).\n"
        "Do not invent places or coordinates. Keep lists short."
    )
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": query}],
        response_format={"type": "json_object"},
    )
    try:
        parsed = json.loads(resp.choices[0].message.content)
    except Exception:
        return _fallback_parse(query, default_n_points)
    parsed.setdefault("keywords", [])
    parsed.setdefault("categories", [])
    parsed.setdefault("n_points", default_n_points)
    parsed.setdefault("region_bbox", None)
    return parsed

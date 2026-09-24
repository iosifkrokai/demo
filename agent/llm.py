"""LLM wrapper for query parsing via DeepInfra API.

Uses DeepInfra's OpenAI-compatible API. Falls back to keyword-based regex parser
if API is unavailable or DEEPINFRA_API_KEY is not set.
"""

from __future__ import annotations

import json
import os
import re

# DeepInfra configuration
DEEPINFRA_API_KEY = os.environ.get("DEEPINFRA_API_KEY")
DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai"
DEFAULT_MODEL = "google/gemini-3.1-pro"

SYSTEM_PROMPT = (
    "Extract structured intent from a Russian-language walking-tour query about Grodno, Belarus.\n"
    "Return JSON with keys: keywords (list of strings), categories "
    "(list of: замок|костёл|церковь|монастырь|дворец|усадьба|парк|музей|памятник|"
    "храм|архитектура|инфраструктура|кладбище), "
    "time_budget_minutes (null, or int 15..480 — total time "
    "the user has, extracted from phrases like «есть 2 часа» or «полдня»).\n"
    "Do not invent places or coordinates. Keep lists short."
)

CATEGORY_SYNONYMS: dict[str, list[str]] = {
    "замок": ["замок", "замки", "крепость", "castle"],
    "костёл": ["костёл", "костёлы", "костел", "костелы"],
    "церковь": ["церковь", "церкви", "church"],
    "монастырь": ["монастырь", "монастыри"],
    "дворец": ["дворец", "дворцы", "дворц"],
    "усадьба": ["усадьба", "усадьбы", "manor", "резиденция"],
    "парк": ["парк", "парки", "парка", "парков", "сквер", "park"],
    "музей": ["музей", "музеи", "музея", "museum", "галерея"],
    "памятник": ["памятник", "памятники", "монумент", "monument", "композиция белые росы"],
    "храм": ["храм", "храмы", "кирха", "синагога", "каплица", "молитвенный"],
    "архитектура": ["архитектура", "архитектурный", "здание", "здания", "дом", "фабрик", "школ", "банк", "театр"],
    "инфраструктура": ["инфраструктура", "мост", "башн", "стадион", "водонапорн", "набережн"],
    "кладбище": ["кладбищ", "некропол"],
}

HISTORICAL_QUERY_MARKERS: dict[str, list[str]] = {
    "история": ["история", "историческ", "истори"],
    "необычный": ["необычн", "нестандарт", "уникальн", "интересн"],
    "съёмка": ["съёмок", "съёмки", "фильм", "белые росы"],
    "советский": ["советск"],
    "неман": ["неман", "набережн", "река"],
}

_LLM_CLIENT = None
_MODEL_NAME = None


def llm_is_ready() -> bool:
    return _LLM_CLIENT is not None


def init() -> None:
    global _LLM_CLIENT, _MODEL_NAME
    if _LLM_CLIENT is not None:
        return

    if not DEEPINFRA_API_KEY:
        print("DEEPINFRA_API_KEY not set; falling back to keyword parser")
        _LLM_CLIENT = None
        return

    import httpx
    _MODEL_NAME = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    _LLM_CLIENT = httpx.Client(
        base_url=DEEPINFRA_BASE_URL,
        headers={"Authorization": f"Bearer {DEEPINFRA_API_KEY}"},
        timeout=30.0,
    )
    print(f"DeepInfra LLM ready: {_MODEL_NAME}")


def _fallback_parse(query: str, default_n_points: int) -> dict:
    q = query.lower()
    categories: list[str] = []

    for cat, syns in CATEGORY_SYNONYMS.items():
        if any(s in q for s in syns):
            if cat not in categories:
                categories.append(cat)

    for marker_key, marker_words in HISTORICAL_QUERY_MARKERS.items():
        if any(w in q for w in marker_words):
            if marker_key == "история":
                for hist_cat in ["замок", "дворец", "монастырь", "костёл", "музей", "архитектура"]:
                    if hist_cat not in categories:
                        categories.append(hist_cat)
            elif marker_key == "необычный":
                if "памятник" not in categories:
                    categories.append("памятник")
            elif marker_key == "неман":
                if "парк" not in categories:
                    categories.append("парк")
                if "инфраструктура" not in categories:
                    categories.append("инфраструктура")
            elif marker_key == "советский":
                if "архитектура" not in categories:
                    categories.append("архитектура")
            elif marker_key == "съёмка":
                if "памятник" not in categories:
                    categories.append("памятник")
                if "монастырь" not in categories:
                    categories.append("монастырь")

    single_word_cats = {
        "замок": ["замок"], "костёл": ["костёл"], "костел": ["костёл"],
        "церковь": ["церковь"], "монастырь": ["монастырь"],
        "дворец": ["дворец"], "усадьба": ["усадьба"],
        "парк": ["парк"], "парки": ["парк"],
        "музей": ["музей"], "музеи": ["музей"],
        "памятник": ["памятник"], "памятники": ["памятник"],
        "храм": ["храм"], "кирха": ["храм"],
    }
    q_stripped = q.strip()
    if q_stripped in single_word_cats:
        categories = single_word_cats[q_stripped]

    keywords = [w for w in re.findall(r"[а-яёa-z]{3,}", q)]

    time_budget_minutes = None
    m = re.search(r"(\d+)\s*(ч|час|часа|часов)", q)
    if m:
        time_budget_minutes = int(m.group(1)) * 60
    if time_budget_minutes is None and re.search(r"\b(час|часа|часов)\b", q):
        time_budget_minutes = 60
    if time_budget_minutes is None:
        m = re.search(r"(\d+)\s*(мин|минут|минуты|минуту)", q)
        if m:
            time_budget_minutes = int(m.group(1))
    if time_budget_minutes is None and re.search(r"полдн\w*", q):
        time_budget_minutes = 240
    if time_budget_minutes is not None:
        time_budget_minutes = max(15, min(time_budget_minutes, 600))

    return {
        "keywords": keywords,
        "categories": categories,
        "time_budget_minutes": time_budget_minutes,
    }


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of a model reply, tolerating ``` fences."""
    if text.startswith("```"):
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break
    return json.loads(text.strip())


def chat_json(system: str, user: str, *, model: str | None = None,
              timeout: float | None = None, max_tokens: int = 256) -> dict:
    """One-shot chat completion that must return a JSON object.

    Shared by query parsing and the reranker so the fence-stripping and error
    contract live in exactly one place. Raises on any transport or parse
    failure — callers are expected to degrade, never to propagate: an LLM
    outage must not turn /routes/generate into a 5xx.
    """
    if _LLM_CLIENT is None:
        raise RuntimeError("llm client not initialised")
    request: dict = {
        "model": model or _MODEL_NAME,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    if timeout is not None:
        request["timeout"] = timeout
    response = _LLM_CLIENT.post("/chat/completions", json=request)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def parse_query(query: str, default_n_points: int = 4) -> dict:
    if _LLM_CLIENT is None:
        return {**_fallback_parse(query, default_n_points), "source": "fallback"}

    try:
        parsed = chat_json(SYSTEM_PROMPT, query, max_tokens=256)
    except Exception as e:
        print(f"LLM API error: {e}; falling back to keyword parser")
        return {**_fallback_parse(query, default_n_points), "source": "fallback"}

    parsed.setdefault("keywords", [])
    parsed.setdefault("categories", [])
    parsed.setdefault("time_budget_minutes", None)
    parsed["source"] = "llm"
    return parsed


def classify_items(
    items: list[str],
    categories: list[str],
    chunk_size: int = 12,
) -> list[str | None]:
    if _LLM_CLIENT is None or not items:
        return [None] * len(items)

    out: list[str | None] = [None] * len(items)
    system = (
        "Ты — ассистент, который классифицирует достопримечательности Гродненской области.\n"
        "Для описания места выбери ровно одну категорию из списка ниже.\n"
        "Верни JSON-объект {\"category\": \"категория\"}. "
        "Если сомневаешься — ставь 'другое'. Никаких пояснений, только JSON.\n\n"
        f"Категории: {' | '.join(categories)}"
    )

    for i in range(0, len(items), chunk_size):
        chunk = items[i : i + chunk_size]
        for j, text in enumerate(chunk):
            idx = i + j
            try:
                response = _LLM_CLIENT.post(
                    "/chat/completions",
                    json={
                        "model": _MODEL_NAME,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": text},
                        ],
                        "temperature": 0.0,
                        "max_tokens": 32,
                    },
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                if content.startswith("```"):
                    parts = content.split("```")
                    for p in parts:
                        p = p.strip()
                        if p.startswith("json"):
                            p = p[4:].strip()
                        if p.startswith("{"):
                            content = p
                            break
                parsed = json.loads(content.strip())
                category = parsed.get("category") if isinstance(parsed, dict) else None
                if isinstance(category, str) and category in categories:
                    out[idx] = category
            except Exception as e:
                print(f"  classify_items item {idx}: {e}")
    return out

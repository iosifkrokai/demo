"""Local LLM wrapper for query parsing. Uses llama-cpp-python in-process.

The model is loaded once at agent startup (see `init()`) and reused for every
request. Default model: Qwen2.5-1.5B-Instruct q4_k_m GGUF (~1 GB), pulled
automatically from HuggingFace on first run and cached in
~/.cache/huggingface/.

Russian support is solid in Qwen2.5 (the family was retrained on multilingual
data including Russian). For CPU-only inference this is the lightest option
that still produces usable JSON.

If `init()` hasn't been called (e.g. running llm.py standalone) or the model
fails to load, we fall back to a regex-based keyword + category extractor.
"""

from __future__ import annotations

import json
import os
import re

# Defaults — overridable via env.
DEFAULT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
DEFAULT_FILE_GLOB = "*q4_k_m.gguf"

SYSTEM_PROMPT = (
    "Extract structured intent from a Russian-language walking-tour query about Grodno.\n"
    "Return JSON with keys: keywords (list of strings), categories "
    "(list of: замок|костёл|церковь|монастырь|дворец|усадьба|парк|музей|памятник|"
    "храм|архитектура|инфраструктура|кладбище), "
    "n_points (int 2..8), time_budget_minutes (null, or int 15..600 — total time "
    "the user has, extracted from phrases like «есть 2 часа» or «полдня»), "
    "region_bbox (null, or an object "
    '{"south": "53.6", "west": "23.7", "north": "53.7", "east": "23.9"} '
    "with coordinate strings).\n"
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

# Keywords that strongly suggest a specific historical/narrative category.
# These queries don't mention a category word directly but are well-served
# by historical place types (замок, дворец, монастырь, музей, костёл).
HISTORICAL_QUERY_MARKERS: dict[str, list[str]] = {
    "история": ["история", "историческ", "истори"],
    "необычный": ["необычн", "нестандарт", "уникальн", "интересн"],
    "съёмка": ["съёмок", "съёмки", "фильм", "белые росы"],
    "советский": ["советск"],
    "неман": ["неман", "набережн", "река"],
}

# NOTE: no JSON-schema "pattern" here — llama-cpp-python cannot compile regex
# patterns into GBNF (llama.cpp aborts with "error parsing grammar" and kills
# the process). Coordinate ranges/axes are validated post-hoc in
# main.sanitize_bbox() instead.
_LAT_DOC = 'latitude string in the Grodno region, e.g. "53.6"'
_LON_DOC = 'longitude string in the Grodno region, e.g. "23.8"'

# Fed to llama-cpp-python as response_format={"type": "json_object", "schema": ...};
# the library compiles it into a GBNF grammar, so the structure (keys, enums,
# coordinate ranges) is guaranteed at sampling time, not just checked after.
PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "categories": {"type": "array", "items": {"enum": list(CATEGORY_SYNONYMS)}, "maxItems": 10},
        "n_points": {"type": "integer", "enum": list(range(2, 9))},
        "time_budget_minutes": {
            "anyOf": [
                {"type": "null"},
                {"type": "integer", "minimum": 15, "maximum": 600},
            ]
        },
        "region_bbox": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "south": {"type": "string", "description": _LAT_DOC},
                        "west": {"type": "string", "description": _LON_DOC},
                        "north": {"type": "string", "description": _LAT_DOC},
                        "east": {"type": "string", "description": _LON_DOC},
                    },
                    "required": ["south", "west", "north", "east"],
                    "additionalProperties": False,
                },
            ]
        },
    },
    "required": ["keywords", "categories", "n_points", "time_budget_minutes", "region_bbox"],
    "additionalProperties": False,
}

_LLM = None  # set by init(); module-level so parse_query() stays cheap


def llm_is_ready() -> bool:
    """Public liveness probe for the LLM. Avoids reaching into module state."""
    return _LLM is not None


def init() -> None:
    """Load the GGUF model into memory. Blocking; call once at agent startup."""
    global _LLM
    if _LLM is not None:
        return
    try:
        from llama_cpp import Llama  # imported lazily so the agent can still start
                                    # without the package on systems where it failed
                                    # to install (e.g. no prebuilt wheel).
    except ImportError as e:
        print(f"llama-cpp-python not available ({e}); falling back to keyword parser")
        _LLM = None
        return

    repo = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    fname = os.environ.get("LLM_FILE", DEFAULT_FILE_GLOB)
    n_ctx = int(os.environ.get("LLM_CTX", "2048"))
    n_threads = int(os.environ.get("LLM_THREADS", "2"))
    print(f"loading local LLM {repo} / {fname} (ctx={n_ctx}, threads={n_threads})...")
    _LLM = Llama.from_pretrained(
        repo_id=repo,
        filename=fname,
        n_ctx=n_ctx,
        n_threads=n_threads,
        verbose=False,
    )
    print("LLM ready.")


def _fallback_parse(query: str, default_n_points: int) -> dict:
    q = query.lower()
    categories: list[str] = []

    # 1. Keyword-based category extraction (most reliable for explicit queries)
    for cat, syns in CATEGORY_SYNONYMS.items():
        if any(s in q for s in syns):
            if cat not in categories:
                categories.append(cat)

    # 2. Historical/narrative query markers → broaden category set
    # These queries don't name a category word but want historical places.
    for marker_key, marker_words in HISTORICAL_QUERY_MARKERS.items():
        if any(w in q for w in marker_words):
            if marker_key == "история":
                # Broaden: include all historically interesting categories
                for hist_cat in ["замок", "дворец", "монастырь", "костёл", "музей", "архитектура"]:
                    if hist_cat not in categories:
                        categories.append(hist_cat)
            elif marker_key == "необычный":
                # "необычные памятники" — prefer памятник, but might get музей too
                if "памятник" not in categories:
                    categories.append("памятник")
            elif marker_key == "неман":
                # River → park + infrastructure near water
                if "парк" not in categories:
                    categories.append("парк")
                if "инфраструктура" not in categories:
                    categories.append("инфраструктура")
            elif marker_key == "советский":
                # Soviet → architecture
                if "архитектура" not in categories:
                    categories.append("архитектура")
            elif marker_key == "съёмка":
                # Film locations → include the monument directly
                if "памятник" not in categories:
                    categories.append("памятник")
                if "монастырь" not in categories:
                    categories.append("монастырь")

    # 3. Single-word category query → override whatever LLM returned
    # e.g. "замок" should return замок, not whatever the model hallucinated
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
    word_to_n = {"пара": 2, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7}
    n_points = next((v for k, v in word_to_n.items() if re.search(rf"\b{k}\b", q)), default_n_points)
    # time budget: «2 часа», «час», «90 минут», «полдня»
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
        "n_points": n_points,
        "time_budget_minutes": time_budget_minutes,
        "region_bbox": None,
    }


def parse_query(query: str, default_n_points: int = 4) -> dict:
    """Parse a free-text Russian query into {keywords, categories, n_points, time_budget_minutes, region_bbox}."""
    if _LLM is None:
        return {**_fallback_parse(query, default_n_points), "source": "fallback"}

    try:
        out = _LLM.create_chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            temperature=0.0,
            response_format={"type": "json_object", "schema": PARSE_SCHEMA},
        )
        parsed = json.loads(out["choices"][0]["message"]["content"])
    except Exception:
        return {**_fallback_parse(query, default_n_points), "source": "fallback"}

    if not isinstance(parsed, dict):
        return {**_fallback_parse(query, default_n_points), "source": "fallback"}
    parsed.setdefault("keywords", [])
    parsed.setdefault("categories", [])
    parsed.setdefault("n_points", default_n_points)
    parsed.setdefault("time_budget_minutes", None)
    parsed.setdefault("region_bbox", None)
    # Sanity-bound n_points to the agent's allowed range.
    try:
        n = int(parsed["n_points"])
        parsed["n_points"] = max(2, min(8, n))
    except (TypeError, ValueError):
        parsed["n_points"] = default_n_points
    parsed["source"] = "llm"
    return parsed


def classify_items(
    items: list[str],
    categories: list[str],
    chunk_size: int = 12,
) -> list[str | None]:
    """Classify each item into one of the allowed categories using the local LLM.

    One item per completion with a grammar-constrained single-category schema:
    batched arrays confused the tiny model into echoing categories across items.
    Returns a parallel list of category strings (or None on per-row failure).
    """
    if _LLM is None or not items:
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
        # One item per prompt, with a task instruction and its own response
        # slot: batched arrays made the tiny model echo categories for other
        # items (зоопарк → «костёл» and the like).
        for j, text in enumerate(chunk):
            idx = i + j
            schema = {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": categories},
                },
                "required": ["category"],
                "additionalProperties": False,
            }
            try:
                resp = _LLM.create_chat_completion(
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": text},
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object", "schema": schema},
                )
                parsed = json.loads(resp["choices"][0]["message"]["content"])
                category = parsed.get("category") if isinstance(parsed, dict) else None
                if isinstance(category, str) and category in categories:
                    out[idx] = category
            except Exception as e:
                print(f"  classify_items item {idx}: {e}")
    return out

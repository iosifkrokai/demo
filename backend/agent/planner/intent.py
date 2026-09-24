"""Step 1 — Intent extraction.

Primary path: OpenRouter Gemini 2.5 Flash via OpenAI-compatible chat completions
with response_format=json_object (constrained JSON). Falls back to a regex
parser on any error or if OPENROUTER_API_KEY is missing.

Returns an IntentResult wrapping an IntentDecision. Latency target: < 1s.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx

from ..config import settings

from ..models import IntentDecision, IntentResult

OPENROUTER_URL = settings.OPENROUTER_URL

INTENT_SYSTEM_PROMPT = """Ты парсер туристических запросов по Гродно на русском языке.
Твоя задача — извлечь из свободного запроса структурированный intent для построения пешего маршрута.

Верни строго JSON со следующими полями:

{
  "intent_type": "discovery" | "specific" | "themed" | "vague",
  "categories_pos": ["замок", "костёл", "церковь", "монастырь", "дворец",
                     "усадьба", "парк", "музей", "памятник", "храм",
                     "архитектура", "инфраструктура", "кладбище"],
  "categories_neg": [... те же категории, что пользователь НЕ хочет видеть ...],
  "keywords_pos": ["парк", "тихий"],         // дополнительные позитивные слова
  "keywords_neg": ["шумный", "толпа"],      // слова, противоречащие запросу
  "named_places": ["горисполком", "Каложская церковь"],
  "narrative": ["контраст эпох", "у воды"], // тематическая ось запроса
  "time_budget_minutes": 180 или null,      // null если не указан
  "era_hint": "any" | "pre1900" | "soviet" | "modern",
  "party_type": "solo" | "family" | "couple" | "group"
}

Правила:
- intent_type=specific: запрос про одну конкретную достопримечательность (имя собственное)
- intent_type=vague: общий запрос без конкретной темы ("покажи Гродно", "достопримечательности")
- intent_type=themed: запрос с явной темой/контрастом ("старое vs советское", "у реки")
- intent_type=discovery: всё остальное (исследование города, прогулка без явной темы)
- categories_pos/neg — ТОЛЬКО из списка в схеме (никаких других значений)
- named_places — ТОЛЬКО имена собственные, упомянутые явно
- Если в запросе "без X" или "кроме X" — добавь в categories_neg
- time_budget_minutes: null если не упомянуто; целое число минут если упомянуто ("3 часа" → 180, "полдня" → 240)
- party_type: "family" если "с детьми", "couple" если "вдвоём", "group" если "с друзьями/группой", иначе "solo"
- Не придумывай лишних полей. Отвечай ТОЛЬКО JSON, без пояснений и markdown-обёрток."""


def extract_intent(query: str, *, force_regex: bool = False) -> IntentResult:
    """Top-level: try Gemini, fall back to regex on any failure.

    force_regex=True skips the LLM (useful in tests and when the key is
    missing — keeps behaviour deterministic).
    """
    t0 = time.perf_counter()

    if not force_regex:
        try:
            decision, raw = _call_gemini(query, settings.GEMINI_TIMEOUT_S)
            latency = int((time.perf_counter() - t0) * 1000)
            return IntentResult(
                decision=decision,
                source="gemini",
                confidence=0.9,
                latency_ms=latency,
                raw_response=raw,
            )
        except Exception as e:
            # Drop through to fallback.
            error = e
    else:
        error = None

    decision = _fallback_intent(query)
    latency = int((time.perf_counter() - t0) * 1000)
    return IntentResult(
        decision=decision,
        source="regex",
        confidence=0.5,
        latency_ms=latency,
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


# ─────────────────────────────────────────────────────────────────────────────
# Gemini path
# ─────────────────────────────────────────────────────────────────────────────

def _call_gemini(query: str, timeout_s: float) -> tuple[IntentDecision, dict]:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(
            f"{OPENROUTER_URL}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": settings.GEMINI_MODEL,
                "messages": [
                    {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": query},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
                "max_tokens": 384,
            },
        )
        r.raise_for_status()
        body = r.json()
        content = body["choices"][0]["message"]["content"]
        parsed = _strip_fences(content)
        # Validate via Pydantic — bad keys → bad values → drop to fallback.
        decision = IntentDecision.model_validate(parsed)
        return decision, parsed


def _strip_fences(text: str) -> Any:
    """LLM may wrap JSON in ```json ... ``` fences. Strip them."""
    s = text.strip()
    if s.startswith("```"):
        # Drop first ``` and trailing ```
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    return json.loads(s)


# ─────────────────────────────────────────────────────────────────────────────
# Regex fallback (ported from agent/llm.py:_fallback_parse)
# ─────────────────────────────────────────────────────────────────────────────

_VALID_CATEGORIES = {
    "замок", "костёл", "церковь", "монастырь", "дворец", "усадьба",
    "парк", "музей", "памятник", "храм", "архитектура",
    "инфраструктура", "кладбище",
}


def _fallback_intent(query: str) -> IntentDecision:
    q = query.lower()
    categories: list[str] = []

    # ── Direct category synonym match ──
    for cat, syns in CATEGORY_SYNONYMS.items():
        if cat not in _VALID_CATEGORIES:
            continue
        if any(s in q for s in syns) and cat not in categories:
            categories.append(cat)

    # ── Historical/thematic markers ──
    for marker_key, marker_words in HISTORICAL_QUERY_MARKERS.items():
        if not any(w in q for w in marker_words):
            continue
        if marker_key == "история":
            for hist_cat in ("замок", "дворец", "монастырь", "костёл", "музей", "архитектура"):
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

    # ── Single-word exact match (cleanest case: "замок", "музеи") ──
    single_word_cats = {
        "замок": "замок", "костёл": "костёл", "костел": "костёл",
        "церковь": "церковь", "монастырь": "монастырь",
        "дворец": "дворец", "усадьба": "усадьба",
        "парк": "парк", "парки": "парк",
        "музей": "музей", "музеи": "музей",
        "памятник": "памятник", "памятники": "памятник",
        "храм": "храм", "кирха": "храм",
    }
    q_stripped = q.strip().rstrip(".,!?")
    if q_stripped in single_word_cats:
        categories = [single_word_cats[q_stripped]]

    keywords = re.findall(r"[а-яёa-z]{3,}", q)

    # ── Time budget ──
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

    # ── Intent type guess ──
    if not categories and not keywords:
        intent_type = "vague"
    elif len(categories) == 1 and not keywords:
        intent_type = "specific"
    elif any(w in q for w in ("контраст", "эпох", "стар", "советск", "разн")):
        intent_type = "themed"
    else:
        intent_type = "discovery"

    # ── Negative constraints from "без X" / "кроме X" ──
    neg_cats: list[str] = []
    neg_pattern = re.search(r"(без|кроме|не)\s+(.+?)(?:,|$)", q)
    if neg_pattern:
        fragment = neg_pattern.group(2)
        for cat in _VALID_CATEGORIES:
            syns = CATEGORY_SYNONYMS.get(cat, [cat])
            if any(s in fragment for s in syns):
                neg_cats.append(cat)
                continue
            # Russian morphology fallback: stem match on first 4 chars.
            # Catches "музеев" <-> "музей", "костёлов" <-> "костёл".
            frag_stem = fragment[:4]
            for s in syns:
                if len(s) >= 4 and s[:4] == frag_stem:
                    neg_cats.append(cat)
                    break

    # ── Era hint ──
    era_hint = "any"
    if re.search(r"советск", q):
        era_hint = "soviet"
    elif re.search(r"(стар\w+|дореволюц|до 1900|до 1917|древн)", q):
        era_hint = "pre1900"

    # ── Party ──
    party_type = "solo"
    if re.search(r"(с детьми|ребен|ребён|малыш)", q):
        party_type = "family"
    elif re.search(r"(вдвоём|с женой|с мужем|парой)", q):
        party_type = "couple"
    elif re.search(r"(с друзьями|группой|компанией)", q):
        party_type = "group"

    # ── Narrative ──
    narrative: list[str] = []
    if re.search(r"контраст", q):
        narrative.append("контраст эпох")
    if re.search(r"у реки|набереж|неман", q):
        narrative.append("у воды")
    if re.search(r"тих|спокойн", q):
        narrative.append("тишина")

    return IntentDecision(
        intent_type=intent_type,
        categories_pos=categories,
        categories_neg=neg_cats,
        keywords_pos=keywords,
        keywords_neg=[],
        named_places=[],
        narrative=narrative,
        time_budget_minutes=time_budget_minutes,
        era_hint=era_hint,
        party_type=party_type,
    )

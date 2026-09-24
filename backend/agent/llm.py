"""Legacy LLM wrapper kept ONLY for offline scripts (scripts/enrich_places.py).

The live agent (agent.main → agent.planner.pipeline) does NOT use this
module — it talks directly to OpenRouter via agent.planner.intent. This file
exists so the historical classification step in enrich_places.py keeps
working.

Anything new should go into agent.planner.* — this module is a shim.
"""

from __future__ import annotations

import json
import os

# OpenRouter configuration
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-3.1-pro"

_LLM_CLIENT = None
_MODEL_NAME = None


def init() -> None:
    """Initialize the OpenRouter HTTP client (used by enrich_places.py)."""
    global _LLM_CLIENT, _MODEL_NAME
    if _LLM_CLIENT is not None:
        return
    if not OPENROUTER_API_KEY:
        print("OPENROUTER_API_KEY not set; LLM calls will fail")
        _LLM_CLIENT = None
        return
    import httpx
    _MODEL_NAME = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    _LLM_CLIENT = httpx.Client(
        base_url=OPENROUTER_BASE_URL,
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        timeout=30.0,
    )
    print(f"OpenRouter LLM ready: {_MODEL_NAME}")


def classify_items(
    items: list[str],
    categories: list[str],
    chunk_size: int = 12,
) -> list[str | None]:
    """Classify each item into one of the given categories via OpenRouter."""
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
        chunk = items[i: i + chunk_size]
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

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
    "(list of: замок|костёл|церковь|монастырь|дворец|усадьба|парк|музей|памятник|городище), "
    "n_points (int 2..8), region_bbox ([south,west,north,east] or null).\n"
    "Do not invent places or coordinates. Keep lists short."
)

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

_LLM = None  # set by init(); module-level so parse_query() stays cheap


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
    n_threads = int(os.environ.get("LLM_THREADS", "4"))
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
    categories = [cat for cat, syns in CATEGORY_SYNONYMS.items() if any(s in q for s in syns)]
    keywords = [w for w in re.findall(r"[а-яёa-z]{3,}", q)]
    word_to_n = {"пара": 2, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7}
    n_points = next((v for k, v in word_to_n.items() if re.search(rf"\b{k}\b", q)), default_n_points)
    return {"keywords": keywords, "categories": categories, "n_points": n_points, "region_bbox": None}


def parse_query(query: str, default_n_points: int = 4) -> dict:
    """Parse a free-text Russian query into {keywords, categories, n_points, region_bbox}."""
    if _LLM is None:
        return _fallback_parse(query, default_n_points)

    try:
        out = _LLM.create_chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(out["choices"][0]["message"]["content"])
    except Exception:
        return _fallback_parse(query, default_n_points)

    if not isinstance(parsed, dict):
        return _fallback_parse(query, default_n_points)
    parsed.setdefault("keywords", [])
    parsed.setdefault("categories", [])
    parsed.setdefault("n_points", default_n_points)
    parsed.setdefault("region_bbox", None)
    # Sanity-bound n_points to the agent's allowed range.
    try:
        n = int(parsed["n_points"])
        parsed["n_points"] = max(2, min(8, n))
    except (TypeError, ValueError):
        parsed["n_points"] = default_n_points
    return parsed

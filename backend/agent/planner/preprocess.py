"""Step 0 — Preprocess the raw user query.

This is pure CPU, < 1ms. It produces a PreprocessedQuery that downstream
steps use to decide whether to take a fast path (single-word queries,
specific named places) or the full LLM-driven pipeline.

Detected features:
  * is_short          — < 3 significant words (keyword-friendly)
  * is_specific       — looks like a single named place
                        (1-2 words, OR quoted, OR contains capitalized word)
  * n_significant_words — count of [а-яёa-z]{3,} tokens
  * fingerprint        — SHA1 of normalized text, for cache keys
"""

from __future__ import annotations

import hashlib
import re

from contracts.planner import PreprocessedQuery

# Words of length ≥ 3, Russian or Latin letters.
WORD_RE = re.compile(r"[а-яёa-z]{3,}")

# Quote marks (RU + EN, opening + closing).
QUOTE_CHARS = "\u00ab\u00bb\u201c\u201d'\""


def preprocess(query: str) -> PreprocessedQuery:
    normalized = query.strip()
    words = WORD_RE.findall(normalized.lower())
    n = len(words)

    is_short = n < 3
    is_specific = _looks_specific(normalized)

    fingerprint = hashlib.sha1(normalized.lower().encode("utf-8")).hexdigest()[:12]

    return PreprocessedQuery(
        raw=query,
        normalized=normalized,
        is_short=is_short,
        is_specific=is_specific,
        fingerprint=fingerprint,
        n_significant_words=n,
    )


def _looks_specific(q: str) -> bool:
    """Heuristic: query targets one specific place, not a class of places."""
    q = q.strip()
    if not q:
        return False

    # 1-2 words → almost always specific.
    parts = q.split()
    if len(parts) <= 2:
        return True

    # Quoted (RU/EN) → user named something explicitly.
    if any(ch in q for ch in QUOTE_CHARS):
        return True

    # Has a capitalized Russian/Latin word ≥ 4 chars (proper noun).
    # Skip first word (sentence-start capital).
    for w in parts[1:]:
        # Strip leading punctuation
        clean = w.lstrip("\u00ab\u201c'\",.()")
        if len(clean) >= 4 and clean[0].isupper():
            # But not the start of a sentence: heuristic — if there's no period
            # before it in the text, treat as proper noun.
            return True

    return False

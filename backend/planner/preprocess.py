"""Step 0 — Preprocess the raw user query.

Pure CPU; produces a PreprocessedQuery that downstream steps pick a path from.
"""

from __future__ import annotations

import hashlib
import re

from contracts.planner import PreprocessedQuery

WORD_RE = re.compile(r"[а-яёa-z]{3,}")

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

    parts = q.split()
    if len(parts) <= 2:
        return True

    if any(ch in q for ch in QUOTE_CHARS):
        return True

    for w in parts[1:]:
        clean = w.lstrip("\u00ab\u201c'\",.()")
        if len(clean) >= 4 and clean[0].isupper():
            return True

    return False

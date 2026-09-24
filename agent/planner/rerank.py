"""Step 3.5 — Cross-encoder reranking.

Local, no API. Uses BAAI/bge-reranker-v2-m3 by default — multilingual
(including Russian), 568M params, ~500MB. ~300-500ms on CPU for top-30.

For each (query, place_description) pair, predict a relevance score in
[0, 1]. Candidates are re-sorted by this score, and `relevance` is
overwritten on each Candidate (rrf_score is preserved for the trace).

Disabled via RERANK_BACKEND=off (skips the call entirely).
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import settings
from ..models import Candidate

log = logging.getLogger(__name__)

_model: Any = None


def _get_model() -> Any:
    """Lazy-load the cross-encoder model once and cache it."""
    global _model
    if _model is not None:
        return _model
    if settings.RERANK_BACKEND != "bge":
        return None
    try:
        from sentence_transformers import CrossEncoder
        log.info("loading cross-encoder: %s", settings.BGE_RERANK_MODEL)
        _model = CrossEncoder(settings.BGE_RERANK_MODEL, max_length=256)
        return _model
    except Exception as e:
        log.warning("cross-encoder load failed, disabling rerank: %s", e)
        return None


def rerank(
    query: str,
    candidates: list[Candidate],
    top_k: int,
) -> list[Candidate]:
    """Re-score candidates and return top-K.

    Behavior:
      - If RERANK_BACKEND=off: returns candidates[:top_k] unchanged.
      - If model fails to load: same as off, with a log warning.
      - Otherwise: predict scores, sort DESC, return top-K.

    Must-visit candidates are force-included (kept regardless of score)
    if they're in the pool; they're re-ordered to the front of the result.
    """
    if not candidates:
        return []
    if len(candidates) <= top_k:
        # Nothing to truncate, but still re-score for downstream MMR.
        pass

    model = _get_model()
    if model is None:
        return candidates[:top_k]

    # Build (query, document) pairs. Document = name + blurb, truncated.
    pairs: list[tuple[str, str]] = []
    for c in candidates:
        doc = c.name
        if c.blurb:
            doc += ". " + c.blurb[:200]
        pairs.append((query[:400], doc))

    try:
        scores = model.predict(pairs, show_progress_bar=False)
    except Exception as e:
        log.warning("rerank predict failed, returning as-is: %s", e)
        return candidates[:top_k]

    for c, s in zip(candidates, scores):
        c.relevance = float(s)
        c.rerank_score = float(s)

    # Must-visit force-include: pin them to the front of the reranked list.
    # Caller's resolve() should already have ensured they're in the pool.
    candidates.sort(key=lambda c: c.relevance, reverse=True)
    return candidates[:top_k]


def warmup() -> bool:
    """Eagerly load the model at startup. Returns True on success."""
    return _get_model() is not None

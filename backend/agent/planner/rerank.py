"""Step 3.5 — Cross-encoder reranking via OpenRouter.

Uses OpenRouter's rerank endpoint (cohere/rerank-v3.5 by default).
Each (query, place_description) pair gets a relevance score in [0, 1].
Candidates are re-sorted by this score, and `relevance` is overwritten.

Disabled via RERANK_BACKEND=off (skips the call entirely).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from ..config import settings
from ..models import Candidate

log = logging.getLogger(__name__)


def rerank(
    query: str,
    candidates: list[Candidate],
    top_k: int,
) -> list[Candidate]:
    """Re-score candidates via OpenRouter rerank and return top-K."""
    if not candidates:
        return []
    if len(candidates) <= top_k:
        pass

    if settings.RERANK_BACKEND == "off":
        return candidates[:top_k]

    if settings.RERANK_BACKEND != "openrouter":
        log.warning("unknown RERANK_BACKEND=%s, skipping", settings.RERANK_BACKEND)
        return candidates[:top_k]

    # Build documents
    documents: list[str] = []
    for c in candidates:
        doc = c.name
        if c.blurb:
            doc += ". " + c.blurb[:200]
        documents.append(doc)

    try:
        scores = _openrouter_rerank(query, documents)
    except Exception as e:
        log.warning("OpenRouter rerank failed, returning as-is: %s", e)
        return candidates[:top_k]

    for c, s in zip(candidates, scores):
        c.relevance = float(s)
        c.rerank_score = float(s)

    candidates.sort(key=lambda c: c.relevance, reverse=True)
    return candidates[:top_k]


def _openrouter_rerank(query: str, documents: list[str]) -> list[float]:
    """Call OpenRouter rerank API. Returns list of scores in input order."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            f"{settings.OPENROUTER_URL}/rerank",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.OPENROUTER_RERANK_MODEL,
                "query": query,
                "documents": documents,
                "return_documents": False,
            },
        )
        r.raise_for_status()
        body = r.json()
        # Results are sorted by relevance desc; remap to input order
        scores: list[float | None] = [None] * len(documents)
        for item in body.get("results", []):
            idx = item["index"]
            scores[idx] = item["relevance_score"]
        # Fill any missing with 0.0
        return [s if s is not None else 0.0 for s in scores]


def warmup() -> bool:
    """No-op for OpenRouter rerank (no model to preload)."""
    return True

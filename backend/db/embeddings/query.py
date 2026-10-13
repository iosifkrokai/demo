"""The query embedding the pipeline uses: the model, behind the process cache."""

from __future__ import annotations

import logging

from core import cache
from db.embeddings import model

log = logging.getLogger(__name__)


def embed_query(text: str) -> list[float]:
    """Embed a search query with the local model, or return no vector."""
    cache_key = cache.embed_key([text], model.MODEL_NAME)
    cached = cache.EMBED_CACHE.get(cache_key)
    if cached:
        return cached[0]

    try:
        vec = model.embed_text(text)
    except Exception as exc:
        log.warning("embed: local model unavailable (%s) — keyword-only retrieval", exc)
        return []
    if not vec:
        log.warning("embed: local model returned no vector — keyword-only retrieval")
        return []
    cache.EMBED_CACHE.put(cache_key, [vec])
    return vec

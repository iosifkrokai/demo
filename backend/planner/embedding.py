"""Embedding a query for the vector signal, with the local model.

The model runs on CPU in this process (see `ml.embeddings`), and the reading is
cached by prompt, so asking the same question twice costs one embed.

No vector is ever an error: an empty list means keyword-only retrieval, which is
a worse answer but an answer.
"""

from __future__ import annotations

import logging

from agent import interpret_cache

log = logging.getLogger(__name__)


def embed_query(text: str) -> list[float]:
    """Embed a search query with the LOCAL model (ml.embeddings).

    Returns [] only when the model cannot be loaded — keyword-only fallback.
    """
    from ml import embeddings

    cache_key = interpret_cache.embed_key([text], embeddings.MODEL_NAME)
    cached = interpret_cache.EMBED_CACHE.get(cache_key)
    if cached:
        return cached[0]

    try:
        vec = embeddings.embed_query(text)
    except Exception as exc:
        log.warning("embed: local model unavailable (%s) — keyword-only retrieval", exc)
        return []
    if not vec:
        log.warning("embed: local model returned no vector — keyword-only retrieval")
        return []
    interpret_cache.EMBED_CACHE.put(cache_key, [vec])
    return vec

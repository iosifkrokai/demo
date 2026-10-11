"""The query embedding the pipeline uses: the model, behind the process cache.

This wrapper could only live next to the model once the cache stopped belonging
to a layer. While `EMBED_CACHE` sat in the agent package, the planner had to own
the wrapper — the seed uses `embeddings.model` directly, and `db` may not reach
up into `agent`.

No vector is ever an error: an empty list means keyword-only retrieval, which is
a worse answer but an answer.
"""

from __future__ import annotations

import logging

from core import cache
from embeddings import model

log = logging.getLogger(__name__)


def embed_query(text: str) -> list[float]:
    """Embed a search query with the local model, or return no vector.

    Returns [] only when the model cannot be loaded — keyword-only fallback.
    """
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

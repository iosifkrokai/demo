"""Step 3.5 — candidate re-scoring. RETIRED with Jev.

The candidate pool used to be re-scored by TypeSafe Jev (`typesafe/jev-1.13`)
via the System One decisions endpoint.  Spec 002 §4.2 keeps Jev only "if it
measurably improves quality" — the golden-set measurement showed Jev does not
pay for itself (compliance was *lower* with Jev on than off), and Jev has been
removed from the backend entirely, so this step no longer calls a model.

What remains is the honest fallback that was always its degraded path:
`rerank()` returns the pool exactly as `retrieve()` produced it (RRF-fused
relevance order) and does NOT truncate to `top_k`.  The pipeline no longer calls
this module at all; it is kept as a documented no-op so nothing that still
imports it breaks, and so the removal is visible rather than silent.
"""

from __future__ import annotations

import logging as _logging

from ..models import Candidate

log = _logging.getLogger(__name__)


def rerank(query: str, candidates: list[Candidate], top_k: int) -> list[Candidate]:
    """Return the candidates in retrieval order — no model, no truncation.

    `top_k` is accepted for signature compatibility and deliberately ignored:
    dropping the tail here would silently trim the pool, which is the one thing
    the no-model path promises not to do.
    """
    if not candidates:
        return []
    log.info("rerank: disabled (Jev removed) — retrieval order kept, no top_k trim")
    return candidates


def warmup() -> bool:
    """No-op: there is no model to preload."""
    return True

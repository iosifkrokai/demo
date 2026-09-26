"""Step 3.5 — Candidate re-scoring via TypeSafe Jev (Score questions).

One batched /systemone call: a Score question per (query, place) pair.
Score levels: 0 = irrelevant … 4 = exactly what the user asked for.
The weighted score is normalised to [0, 1] and overwrites relevance.

The TypeSafe re-ranking cookbook validates this pattern: one question per
query-candidate pair, batched in a single request — cheaper and faster
than per-pair calls.

Degraded mode: with no OPENROUTER_API_KEY, or an upstream that times out
or 5xx, the step is SKIPPED and the candidates come back in retrieval
order. Re-ranking is a refinement of an order that is already usable, so
losing it costs quality, not the request — one WARNING per call, never
one per candidate.
"""

from __future__ import annotations

import logging as _logging

from .. import constants, jev
from ..models import Candidate

log = _logging.getLogger(__name__)

# Score criteria — 5 ordered levels.
_LEVELS = [
    "irrelevant — the place does not match the query at all",
    "weak — tangentially related",
    "moderate — related but not a highlight for this query",
    "good — clearly matches what the user asked for",
    "perfect — exactly the kind of place the user asked for",
]
_MAX_LEVEL = len(_LEVELS) - 1


def rerank(query: str, candidates: list[Candidate], top_k: int) -> list[Candidate]:
    """Score candidates against the query via one batched Jev call.

    Returns the pool untouched (retrieval order, no top_k truncation) when
    Jev cannot answer — see the module docstring.  Otherwise the scores
    overwrite relevance and the pool is cut to top_k, exactly as before.
    """
    if not candidates:
        return []

    if not jev.available():
        log.warning("rerank: no OPENROUTER_API_KEY — skipped, retrieval order kept")
        return candidates

    places = [
        {"id": str(i), "name": c.name, "description": (c.blurb or "")[:200]}
        for i, c in enumerate(candidates)
    ]
    state = [{"query": query}, {"places": places}]
    questions = {
        f"rel_{i}": {
            "type": "score",
            "instructions": {
                "question": "How well does place `place` match the user's `query`?",
                "place": p,
            },
            "criteria": _LEVELS,
        }
        for i, p in enumerate(places)
    }

    try:
        answers = jev.ask(state, questions, model=constants.JEV_MODEL)
    except jev.JevError as exc:
        log.warning("rerank: Jev unavailable (%s) — skipped, retrieval order kept", exc)
        return candidates

    for i, c in enumerate(candidates):
        raw = jev.score(answers[f"rel_{i}"])
        norm = raw / _MAX_LEVEL if _MAX_LEVEL else 0.0
        c.relevance = norm
        c.rerank_score = norm

    candidates.sort(key=lambda c: c.relevance, reverse=True)
    return candidates[:top_k]


def warmup() -> bool:
    """No-op: Jev needs no preloading."""
    return True

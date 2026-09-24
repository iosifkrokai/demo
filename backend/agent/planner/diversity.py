"""Step 4 — MMR (Maximal Marginal Relevance) diversity reranking.

Classical Carbonell & Goldstein 1998: select candidates that are both
relevant AND diverse. The scoring formula is:

    score(i) = λ * relevance(i) - (1 - λ) * max_similarity(i, selected)

where similarity is computed in the *embedding* space (cosine) rather
than over tf-idf tokens — that's the modern variant that works with
pre-trained multilingual embeddings.

λ controls the relevance/diversity trade-off:
  * λ → 1.0 → ignore diversity (top-K by relevance)
  * λ → 0.5 → balanced
  * λ → 0.0 → pure diversity (round-robin over clusters)

Default 0.7 (MMR_LAMBDA) is the most common starting point in the literature.

Must-visit candidates are force-included first; the MMR loop runs on
the remaining pool to fill up to `n`.
"""

from __future__ import annotations

import numpy as np

from .. import constants
from ..config import settings
from ..models import Candidate, ResolvedConstraints
from ..search import fetch_embeddings


def mmr_select(
    candidates: list[Candidate],
    n: int,
    *,
    lam: float | None = None,
    db=None,
    constraints: ResolvedConstraints | None = None,
) -> list[Candidate]:
    """Pick `n` diverse+relevant candidates from the input pool.

    `db` is required for embedding lookup (used to compute pairwise similarity).
    If embeddings cannot be loaded for some candidates, falls back to a
    relevance-only truncation (no diversity penalty).
    """
    if lam is None:
        lam = constants.MMR_LAMBDA
    if not candidates or len(candidates) <= n:
        return list(candidates)

    # ── Step 1: build the embedding matrix ──
    embs = fetch_embeddings(db, [c.id for c in candidates]) if db else {}
    has_embs = len(embs) == len(candidates)
    if not has_embs:
        # Fallback: pure-relevance truncation, preserving must-visit.
        return _fallback_truncate(candidates, n, constraints)

    E = np.array([embs[c.id] for c in candidates], dtype=np.float32)
    # L2-normalize → cosine similarity = dot product.
    norms = np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
    E_norm = E / norms
    sim_matrix = E_norm @ E_norm.T  # NxN, diagonal = 1

    # ── Step 2: normalize relevance scores to [0, 1] ──
    rel = np.array([c.relevance for c in candidates], dtype=np.float32)
    r_min, r_max = float(rel.min()), float(rel.max())
    if r_max - r_min > 1e-9:
        rel_norm = (rel - r_min) / (r_max - r_min)
    else:
        rel_norm = np.ones_like(rel) * 0.5

    # ── Step 3: must-visit pre-selection ──
    must_ids = set(constraints.must_visit_ids) if constraints else set()
    must_indices: list[int] = []
    rest_indices: list[int] = []
    for i, c in enumerate(candidates):
        if c.id in must_ids:
            must_indices.append(i)
        else:
            rest_indices.append(i)

    # Keep must-visit at the front (up to n).
    selected = must_indices[:n]
    available = [i for i in rest_indices if i not in selected]

    # ── Step 4: MMR greedy loop ──
    while len(selected) < n and available:
        best_idx: int | None = None
        best_score = -float("inf")
        for i in available:
            # max cosine sim to any already-selected candidate
            max_sim = max(sim_matrix[i, s] for s in selected) if selected else 0.0
            score = lam * rel_norm[i] - (1 - lam) * max_sim
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx is None:
            break
        selected.append(best_idx)
        available.remove(best_idx)

    # Return in selection order.
    return [candidates[i] for i in selected]


def _fallback_truncate(
    candidates: list[Candidate],
    n: int,
    constraints: ResolvedConstraints | None,
) -> list[Candidate]:
    """If embeddings unavailable, pick must-visits first, then by relevance."""
    must_ids = set(constraints.must_visit_ids) if constraints else set()
    must = [c for c in candidates if c.id in must_ids][:n]
    rest = sorted(
        [c for c in candidates if c.id not in must_ids],
        key=lambda c: c.relevance,
        reverse=True,
    )
    return (must + rest)[:n]


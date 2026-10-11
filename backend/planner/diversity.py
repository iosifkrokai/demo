"""Step 4 — MMR (Maximal Marginal Relevance) diversity reranking.

Relevance and diversity; must-visits first, then MMR fills up to `n`.
"""

from __future__ import annotations

import numpy as np

from core import constants
from db.store.places import PostgresPlaceRepository
from planner.models import Candidate, ResolvedConstraints


def mmr_select(
    candidates: list[Candidate],
    n: int,
    *,
    lam: float | None = None,
    places: PostgresPlaceRepository | None = None,
    constraints: ResolvedConstraints | None = None,
) -> list[Candidate]:
    """Pick `n` diverse+relevant candidates from the input pool.

    `places` is needed for the embedding lookup; without it, relevance-only.
    """
    if lam is None:
        lam = constants.MMR_LAMBDA
    if not candidates or len(candidates) <= n:
        return list(candidates)

    embs = places.embeddings_by_ids([c.id for c in candidates]) if places else {}
    has_embs = len(embs) == len(candidates)
    if not has_embs:
        return _fallback_truncate(candidates, n, constraints)

    emb = np.array([embs[c.id] for c in candidates], dtype=np.float32)
    norms = np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9
    emb_norm = emb / norms
    sim_matrix = emb_norm @ emb_norm.T

    rel = np.array([c.relevance for c in candidates], dtype=np.float32)
    r_min, r_max = float(rel.min()), float(rel.max())
    rel_norm = (rel - r_min) / (r_max - r_min) if r_max - r_min > 1e-09 else np.ones_like(rel) * 0.5

    must_ids = set(constraints.must_visit_ids) if constraints else set()
    must_indices: list[int] = []
    rest_indices: list[int] = []
    for i, c in enumerate(candidates):
        if c.id in must_ids:
            must_indices.append(i)
        else:
            rest_indices.append(i)

    selected = must_indices[:n]
    available = [i for i in rest_indices if i not in selected]

    while len(selected) < n and available:
        best_idx: int | None = None
        best_score = -float("inf")
        for i in available:
            max_sim = max(sim_matrix[i, s] for s in selected) if selected else 0.0
            score = lam * rel_norm[i] - (1 - lam) * max_sim
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx is None:
            break
        selected.append(best_idx)
        available.remove(best_idx)

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


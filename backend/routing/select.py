"""Pool shaping: fusing ranked lists into one order."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from core import constants


def rrf_fuse(
    rankings: Iterable[list[tuple[int, float]]],
    k: int = constants.RRF_K,
) -> dict[int, float]:
    """Fuse multiple ranked lists into a single score per id."""
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, (pid, _) in enumerate(ranking):
            scores[pid] += 1.0 / (k + rank + 1)
    return dict(scores)

"""Pool shaping: fusing ranked lists into one order.

Pure over ids and scores — no database, no model. What a signal *is* (a vector
search, a category lookup, a proximity scan) belongs to the caller; this module
only decides how several of them combine.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable


def rrf_fuse(
    rankings: Iterable[list[tuple[int, float]]],
    k: int = 60,
) -> dict[int, float]:
    """Fuse multiple ranked lists into a single score per id.

    score(id) = sum over signals of 1 / (k + rank); no score normalization needed.
    """
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, (pid, _) in enumerate(ranking):
            scores[pid] += 1.0 / (k + rank + 1)
    return dict(scores)

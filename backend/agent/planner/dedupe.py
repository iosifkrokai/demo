"""Physical duplicate removal — the same POI stored more than once.

The base stores a place twice whenever a curated row and an OSM row disagree on
the name or the precise coordinates ("Новый замок (дворец Стефана Батория)" vs
"Новый замок"), which put the same sight into a route twice.  Candidates arrive
relevance-ordered, so the highest-ranked member of each cluster wins.
"""

from __future__ import annotations

import re as _re
from typing import Any

from contracts.planner import Candidate
from domain import constants

from .geo import _TRACE_NAMES_MAX, _distance_pt_m


def _norm_name(name: str) -> str:
    """Lowercase, drop parentheticals and punctuation — for POI-name matching."""
    n = _re.sub(r"\([^)]*\)", " ", (name or "").lower())
    n = _re.sub(r"[^0-9a-zа-яё]+", " ", n)
    return " ".join(n.split())


def _drop_duplicates(candidates: list[Candidate], radius_m: float) -> list[Candidate]:
    """Drop places that sit on top of an already-kept, better-ranked place.

    The base stores the same POI more than once whenever a curated row and an
    OSM row disagree on the name or the precise coordinates ("Новый замок
    (дворец Стефана Батория)" vs "Новый замок"; "Дом-музей Адама Мицкевича"
    twice, 340 m apart), which put the same sight into a route twice.
    Candidates arrive relevance-ordered, so the highest-ranked member of each
    cluster wins.  A candidate is dropped when it is within `radius_m` of a
    kept place, or when it carries the same normalised name and lies within
    `constants.DUPLICATE_NAME_RADIUS_M`.
    """
    kept: list[Candidate] = []
    for cand in candidates:
        cand_name = _norm_name(cand.name)
        duplicate = False
        for k in kept:
            dist = _distance_pt_m(cand.lat, cand.lon, k.lat, k.lon)
            if dist < radius_m:
                duplicate = True
                break
            if (
                cand_name
                and cand_name == _norm_name(k.name)
                and dist < constants.DUPLICATE_NAME_RADIUS_M
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(cand)
    return kept


def _dupe_pairs(before: list[Any], after: list[Any], radius_m: float) -> list[dict[str, Any]]:
    """Which stored duplicate was folded into which surviving place.

    ``_drop_duplicates`` keeps the best-ranked row of a cluster; the fact that
    «убрано 3» came from one castle stored under two names, or from a museum
    whose two rows sit 340 m apart, is what tells a reader the step worked as
    intended rather than ate three sights. The pairing rule is the one that
    function uses — within ``radius_m`` of a kept place, or the same normalised
    name within ``DUPLICATE_NAME_RADIUS_M`` — reproduced here rather than
    returned from it, so the pipeline's own signature stays as it was.
    """
    kept = list(after)
    kept_ids = {c.id for c in kept}
    pairs: list[dict[str, Any]] = []
    for cand in before:
        if cand.id in kept_ids:
            continue
        cand_name = _norm_name(cand.name)
        for k in kept:
            dist = _distance_pt_m(cand.lat, cand.lon, k.lat, k.lon)
            if dist < radius_m or (
                cand_name
                and cand_name == _norm_name(k.name)
                and dist < constants.DUPLICATE_NAME_RADIUS_M
            ):
                pairs.append(
                    {
                        "dropped": cand.name,
                        "into": k.name,
                        "m": round(dist),
                    }
                )
                break
    return pairs[:_TRACE_NAMES_MAX]

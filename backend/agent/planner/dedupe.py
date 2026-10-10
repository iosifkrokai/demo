"""Physical duplicate removal — the same POI stored more than once.

Candidates arrive relevance-ordered, so the highest-ranked member wins.
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

    Drops within `radius_m`, or same normalised name within `DUPLICATE_NAME_RADIUS_M`.
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

    Same pairing rule as ``_drop_duplicates``, capped at ``_TRACE_NAMES_MAX``.
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

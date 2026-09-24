"""Step 2 — Resolve constraints.

Merges IntentDecision with explicit client parameters:
  * Time budget: explicit client > LLM > default (clamped to [MIN, MAX]).
  * Bbox:        explicit client wins; otherwise None (whole Grodno).
  * Named places → must_visit_ids via keyword search fallback.

Outputs ResolvedConstraints consumed by retrieve/optimize/etc.
"""

from __future__ import annotations

import psycopg

from ..config import settings
from .intent import CATEGORY_SYNONYMS
from ..models import IntentResult, ResolvedConstraints
from ..search import _keyword_search


def resolve(
    intent: IntentResult,
    *,
    explicit_time_budget: int | None = None,
    explicit_bbox: list[float] | None = None,
    db: psycopg.Connection,
) -> ResolvedConstraints:
    d = intent.decision

    # ── Time budget: explicit > LLM > default ──
    if explicit_time_budget is not None:
        budget = explicit_time_budget
    elif d.time_budget_minutes is not None:
        budget = d.time_budget_minutes
    else:
        budget = settings.DEFAULT_TIME_BUDGET_MIN
    budget = max(settings.MIN_TIME_BUDGET_MIN, min(budget, settings.MAX_TIME_BUDGET_MIN))

    # ── Bbox: explicit wins; else None. Format: (W, S, E, N) — matches ST_MakeEnvelope.
    bbox = tuple(explicit_bbox) if explicit_bbox else None
    if bbox is not None and len(bbox) == 4:
        # Reorder from [south, west, north, east] (HTTP) to (W, S, E, N).
        s, w, n, e = bbox
        bbox = (w, s, e, n)

    # ── Named places → must_visit_ids ──
    must_visit_ids = _resolve_named_places(d.named_places, db) if d.named_places else []

    # ── Build must_visit_keywords (used by retrieval as a strong positive signal) ──
    must_visit_keywords = _expand_categories_to_keywords(d.categories_pos)
    must_visit_keywords.extend(d.keywords_pos)

    # ── Era hint: pass through ──
    era_hint = d.era_hint if d.era_hint in ("any", "pre1900", "soviet", "modern") else "any"

    return ResolvedConstraints(
        must_visit_ids=must_visit_ids,
        optional_categories=list(d.categories_pos),
        forbidden_categories=list(d.categories_neg),
        forbidden_keywords=list(d.keywords_neg),
        time_budget_minutes=budget,
        bbox=bbox,
        era_hint=era_hint,
        party_type=d.party_type,
        intent_type=d.intent_type,
        must_visit_keywords=must_visit_keywords,
        query_keywords=list(d.keywords_pos),
    )


def _resolve_named_places(names: list[str], db: psycopg.Connection) -> list[int]:
    """Match named place strings to place IDs via keyword search.

    For each name:
      1. Keyword search on `places.name` (handles 'горисполком', 'Аптека-музей').
      2. Take the top-1 result.
      3. Dedupe by id (preserve order).
    """
    out: list[int] = []
    seen: set[int] = set()
    for name in names:
        if not name or not name.strip():
            continue
        rows = _keyword_search(db, name, limit=3)
        if rows:
            top = rows[0]
            if top["id"] not in seen:
                seen.add(top["id"])
                out.append(top["id"])
    return out


def _expand_categories_to_keywords(categories: list[str]) -> list[str]:
    """Convert LLM categories into additional Russian synonyms for retrieval."""
    out: list[str] = []
    for c in categories:
        syns = CATEGORY_SYNONYMS.get(c, [c])
        for s in syns:
            if s and s not in out:
                out.append(s)
    return out

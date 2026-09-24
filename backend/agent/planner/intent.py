"""Step 1 — Intent extraction via TypeSafe Jev (System One).

One /systemone call with typed questions against the raw query:
    * categories_pos  — noul per category (13 questions, batched)
    * categories_neg  — noul per category against "does the user NOT want X"
    * intent_type     — choice (discovery/specific/themed/vague)
    * party_type      — choice (solo/family/couple/group)
    * era_hint        — choice (any/pre1900/soviet/modern)
    * time_hours      — score 0..8 (0 = not mentioned) → minutes in code

Jev's primary training language is English (docs), so questions and
criteria are written in English while the state (the user query) stays
Russian — live-tested: "замки и костёлы Новогрудка" → castles 0.98,
churches 0.94.

No fallbacks by design: on failure the caller surfaces a 502 — a silently
degraded route is worse than an explicit error.
"""

from __future__ import annotations

import time

from .. import constants, jev
from ..models import IntentDecision, IntentResult

# Probability threshold: a category counts as requested above this.
_CAT_YES = 0.5
# Score levels for the time budget: hours 0..8 (0 means "not mentioned").
_TIME_LEVELS = ["not mentioned", "1h", "2h", "3h", "4h", "5h", "6h", "7h", "8h+"]

_QUESTIONS: dict[str, dict] = {
    **{
        f"cat_{cat}": {
            "type": "noul",
            "instructions": "Does this tourist query ask to visit places of this type?",
            "criteria": {
                "true": f"`{cat}` — yes, the user wants to see this kind of place",
                "false": "no mention of this kind of place",
            },
        }
        for cat in constants.CATEGORIES
    },
    **{
        f"neg_{cat}": {
            "type": "noul",
            "instructions": "Does this tourist query explicitly EXCLUDE this kind of place (phrases like 'без X', 'кроме X', 'не хочу X')?",
            "criteria": {
                "true": f"`{cat}` — explicitly excluded",
                "false": "not excluded",
            },
        }
        for cat in constants.CATEGORIES
    },
    "intent_type": {
        "type": "choice",
        "instructions": "What kind of tourist query is this?",
        "criteria": {
            "specific": "asks about one concrete named place",
            "themed": "explicit theme or contrast (e.g. old vs soviet, by the river)",
            "discovery": "general exploration / a walk without a strong theme",
            "vague": "no clear ask at all",
        },
    },
    "party_type": {
        "type": "choice",
        "instructions": "Who is travelling according to the query?",
        "criteria": {
            "family": "with children / family",
            "couple": "two people, romantic wording",
            "group": "friends or a group",
            "solo": "one person or unspecified",
        },
    },
    "era_hint": {
        "type": "choice",
        "instructions": "Which historical era does the query emphasise?",
        "criteria": {
            "pre1900": "old / medieval / pre-revolutionary explicitly requested",
            "soviet": "soviet era explicitly requested",
            "modern": "modern / contemporary explicitly requested",
            "any": "no era preference",
        },
    },
    "time_hours": {
        "type": "score",
        "instructions": "How many hours of sightseeing does the query budget (phrases like '3 часа', 'полдня', 'весь день')? Use 0 only if not mentioned.",
        "criteria": _TIME_LEVELS,
    },
}


def extract_intent(query: str) -> IntentResult:
    """Typed intent decision in one Jev call. Raises on upstream failure."""
    t0 = time.perf_counter()

    answers = jev.ask(query, _QUESTIONS)

    cat_pos = [
        cat for cat in constants.CATEGORIES
        if jev.noul(answers[f"cat_{cat}"]) >= _CAT_YES
    ]
    cat_neg = [
        cat for cat in constants.CATEGORIES
        if jev.noul(answers[f"neg_{cat}"]) >= 0.7
    ]

    hours = jev.score(answers["time_hours"])
    time_budget = int(round(hours * 60)) if hours >= 0.5 else None

    # Jev returns free-form strings; validate against the taxonomy and fail
    # loud on drift (stray values mean the model or taxonomy changed).
    itype = jev.choice(answers["intent_type"])
    if itype not in constants.INTENT_TYPES:
        raise ValueError(f"jev: unknown intent_type {itype!r}")
    era = jev.choice(answers["era_hint"])
    if era not in constants.ERA_HINTS:
        raise ValueError(f"jev: unknown era_hint {era!r}")
    party = jev.choice(answers["party_type"])
    if party not in constants.PARTY_TYPES:
        raise ValueError(f"jev: unknown party_type {party!r}")
    known = set(constants.CATEGORIES)
    pos = [c for c in cat_pos if c in known]
    neg = [c for c in cat_neg if c in known]

    decision = IntentDecision(
        intent_type=itype,  # type: ignore[arg-type]
        categories_pos=pos,  # type: ignore[arg-type]
        categories_neg=neg,  # type: ignore[arg-type]
        keywords_pos=[],   # keyword signal comes from retrieval, not the LLM
        keywords_neg=[],
        named_places=[],
        narrative=[],
        time_budget_minutes=time_budget,
        era_hint=era,  # type: ignore[arg-type]
        party_type=party,  # type: ignore[arg-type]
    )

    return IntentResult(
        decision=decision,
        source="jev",
        confidence=max(
            (a.get("confidence", 0.0) for a in answers.values()
             if isinstance(a, dict)),
            default=0.0,
        ),
        latency_ms=int((time.perf_counter() - t0) * 1000),
        raw_response=answers,
    )

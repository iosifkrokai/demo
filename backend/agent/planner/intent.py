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

import logging as _logging
import re as _re
import time

from .. import constants, jev
from ..models import IntentDecision, IntentResult

log = _logging.getLogger(__name__)

# Probability threshold: a category counts as requested above this.
_CAT_YES = 0.5
# Score levels for the time budget: hours 0..8 (0 means "not mentioned").
_TIME_LEVELS = ["not mentioned", "1h", "2h", "3h", "4h", "5h", "6h", "7h", "8h+"]

# The time budget exists only when the USER stated it. Jev scores generously —
# "хочу посмотреть все костёлы области" came back as 2 hours — and an invented
# budget trims a perfectly good route down to two stops, so a budget is kept
# only when the query itself carries a time expression. No expression → no
# limit at all, and the whole route is built.
_TIME_PHRASE_RE = _re.compile(
    r"\d+\s*(?:час|мин)|"
    r"пол\s*дня|полдня|"
    r"(?:весь|целый|полный)\s+день|"
    r"\b(?:час|часа|часов|минут|минуты)\b|"
    r"\bдень\b|\bутр[оа]\b|\bвечер\w*|\bноч\w*|"
    r"\bнедел\w*|"
    r"быстр\w*|коротк\w*|недолг\w*|"
    r"на\s+выходн\w*",
    _re.I,
)

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
    "mentions_named_place": {
        "type": "noul",
        "instructions": "Does the query explicitly name one specific place or town (proper noun, e.g. 'Мирский замок', 'Новогрудок', 'Коложская церковь')?",
        "criteria": {
            "true": "a proper name of a specific place/town is present",
            "false": "no specific place named",
        },
    },
    "search_scope": {
        "type": "choice",
        "instructions": "How wide is the area the user wants to cover?",
        "criteria": {
            "town": "one town / a spot inside a town ('замки Гродно', 'костёлы Новогрудка')",
            "district": "a town with its rural surroundings, or one named district",
            "region": "an entire administrative region / voblast, no single town ('все костёлы Гродненской области', 'что посмотреть по всей области')",
        },
    },
    "time_hours": {
        "type": "score",
        "instructions": "How many hours of sightseeing does the query budget (phrases like '3 часа', 'полдня', 'весь день')? Use 0 only if not mentioned.",
        "criteria": _TIME_LEVELS,
    },
}

# Stop-list of capitalised words that look like region/administrative names
# but are not place names tourists would visit.  Kept in lower-case so the
# comparison against `.lower()` tokens is correct.
# Covers nominative, genitive, dative, instrumental, and prepositional forms.
_PLACE_STOP_LIST: frozenset[str] = frozenset({
    "гродненская", "гродненской", "гродненскому", "гродненском",
    "область", "области", "областью", "областях",
})


def extract_intent(query: str) -> IntentResult:
    """Typed intent decision in one Jev call. Raises on upstream failure."""
    t0 = time.perf_counter()

    answers = jev.ask(query, _QUESTIONS)

    # A taxonomy entry the model did not answer about is simply "not mentioned"
    # (the category list can grow between prompts; never crash on a missing key).
    cat_pos = [
        cat for cat in constants.CATEGORIES
        if jev.noul(answers.get(f"cat_{cat}") or {"noul": 0.0}) >= _CAT_YES
    ]
    cat_neg = [
        cat for cat in constants.CATEGORIES
        if jev.noul(answers.get(f"neg_{cat}") or {"noul": 0.0}) >= 0.7
    ]

    hours = jev.score(answers["time_hours"])
    time_budget = int(round(hours * 60)) if hours >= 0.5 else None
    if time_budget is not None and not _TIME_PHRASE_RE.search(query):
        # The model guessed a duration the user never gave. Treat as unlimited:
        # no budget means the whole route is built, not trimmed to fit a number
        # nobody asked for.
        log.info("intent: dropping inferred time budget (%s min) — no time in query", time_budget)
        time_budget = None

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
    scope = jev.choice(answers["search_scope"])
    if scope not in constants.SEARCH_SCOPES:
        raise ValueError(f"jev: unknown search_scope {scope!r}")
    known = set(constants.CATEGORIES)
    pos = [c for c in cat_pos if c in known]
    neg = [c for c in cat_neg if c in known]

    # Proper-noun candidates for must-visit resolution: capitalised words
    # inside the Russian query (works for toponyms and place names).
    #
    # Known limitation — sentence-initial verbs
    # The regex [А-ЯЁ][а-яё\-]{2,} captures any capitalised ≥3-char word, so
    # a query-initial verb ("Хочу к …") is included.  These tokens are
    # harmless because _resolve_named_places calls _keyword_search per token;
    # a verb returns no DB rows → the must_visit_ids list stays clean.
    # The pipeline then falls back to top-RRF as the geo anchor, which is
    # the correct behaviour for a discovery-style query with no named place.
    tokens = _re.findall(r"[А-ЯЁ][а-яё\-]{2,}", query)
    named = [t for t in tokens if t.lower() not in _PLACE_STOP_LIST]

    decision = IntentDecision(
        intent_type=itype,  # type: ignore[arg-type]
        categories_pos=pos,  # type: ignore[arg-type]
        categories_neg=neg,  # type: ignore[arg-type]
        keywords_pos=[],   # keyword signal comes from retrieval, not the LLM
        keywords_neg=[],
        named_places=named,
        narrative=[],
        time_budget_minutes=time_budget,
        era_hint=era,  # type: ignore[arg-type]
        party_type=party,  # type: ignore[arg-type]
        search_scope=scope,  # type: ignore[arg-type]
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

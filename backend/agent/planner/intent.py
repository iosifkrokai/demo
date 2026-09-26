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

Degraded mode (no OpenRouter key, or an upstream that times out / 5xx)
    The step falls back to `fallback_intent`: a deterministic, dependency-free
    parse of the same query text.  It fills in only what the text itself says
    — categories via the SAME shared keyword→category map retrieval uses
    (planner/resolve.py `CATEGORY_SYNONYMS`, inverted — the taxonomy lives
    in one place, never duplicated here), an explicit time budget
    ("за 3 часа"), the named-place tokens that resolve through the DB, a
    region-wide scope.  A degraded route is worse than a good model answer,
    but it is never a failed request: with no key the agent still answers
    every /routes/generate.
"""

from __future__ import annotations

import logging as _logging
import re as _re
import time

from .. import constants, jev
from ..models import IntentDecision, IntentResult
from .preprocess import WORD_RE
from .resolve import CATEGORY_SYNONYMS

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


# ─────────────────────────────────────────────────────────────────────────────
# Degraded mode — the same query, parsed without a model
# ─────────────────────────────────────────────────────────────────────────────

# An explicit duration in the query text.  The model-free path may only keep a
# budget the user actually stated, so the patterns demand a number (or a
# fixed-length phrase) — "на пару часов" or a bare "час" is ambiguous and is
# treated as "no budget", exactly like a query that never mentions time.
_FALLBACK_HOURS_RE = _re.compile(r"(\d{1,2})\s*(?:час\w*|ч(?![а-яё]))", _re.I)
_FALLBACK_MINUTES_RE = _re.compile(r"(\d{1,3})\s*(?:минут\w*|мин(?![а-яё]))", _re.I)
# Whole phrases that name a duration without a number.
_FALLBACK_DAY_RE = _re.compile(
    r"(?:весь|целый|полный|на\s+весь)\s+день|сутк\w*|пол\s*дня|полдня",
    _re.I,
)
_FALLBACK_FULL_DAY_RE = _re.compile(r"(?:весь|целый|полный)\s+день|сутк\w*", _re.I)
# How wide the ask is.  "region" is the only value the pipeline branches on
# (it skips the geo focus and drives instead of walking), so the region words
# are the ones worth reading off the text; "район" is reported as a district.
_FALLBACK_REGION_RE = _re.compile(
    r"област\w*|регион\w*|кра[йея]\b|по\s+все[йм][\w\s]*|всю\s+область",
    _re.I,
)
_FALLBACK_DISTRICT_RE = _re.compile(r"район\w*", _re.I)


def _named_place_tokens(query: str) -> list[str]:
    """Proper-noun candidates for must-visit resolution: capitalised words
    inside the Russian query (works for toponyms and place names).

    Known limitation — sentence-initial verbs
    The regex [А-ЯЁ][а-яё\\-]{2,} captures any capitalised ≥3-char word, so
    a query-initial verb ("Хочу к …") is included.  These tokens are
    harmless because _resolve_named_places calls _keyword_search per token;
    a verb returns no DB rows → the must_visit_ids list stays clean.
    The pipeline then falls back to top-RRF as the geo anchor, which is
    the correct behaviour for a discovery-style query with no named place.
    """
    tokens = _re.findall(r"[А-ЯЁ][а-яё\-]{2,}", query)
    return [t for t in tokens if t.lower() not in _PLACE_STOP_LIST]


# The shared keyword→category taxonomy (resolve.CATEGORY_SYNONYMS), inverted:
# every surface form the map knows → its category.  Multi-word retrieval
# phrases ("гостевой дом") are skipped — the fallback matches query WORDS,
# and a phrase is not a word.  A form listed under two categories would be
# ambiguous; the map keeps them disjoint (asserted by the tests).
_KEYWORD_TO_CATEGORY: dict[str, str] = {
    form: cat
    for cat, forms in CATEGORY_SYNONYMS.items()
    for form in forms
    if " " not in form
}


def _fallback_categories(query: str) -> list[str]:
    """Categories the query text itself states, read off the shared map.

    Deterministic word match on the lowercased query: «замкам» → "замок",
    «костёлам» → "костёл", «кофейне» → "кафе".  A word the map does not
    know simply yields nothing, so a themed query with no category word
    returns an empty set — the honest answer, exactly like the model-free
    scope/time handling (nothing stated → nothing invented).
    """
    cats: list[str] = []
    seen: set[str] = set()
    for word in _re.findall(r"[а-яё]+", query.lower()):
        cat = _KEYWORD_TO_CATEGORY.get(word)
        if cat and cat not in seen:
            seen.add(cat)
            cats.append(cat)
    return cats


def _fallback_time_budget(query: str) -> int | None:
    """Minutes of sightseeing the query itself budgets, or None.

    Only what the text states: "за 3 часа" → 180, "на 90 минут" → 90,
    "на полдня" → 240, "на весь день" → 480.  resolve() clamps the result
    to [MIN_BUDGET_MIN, MAX_BUDGET_MIN], so a wild number is bounded.
    """
    m = _FALLBACK_HOURS_RE.search(query)
    if m:
        return int(m.group(1)) * 60
    m = _FALLBACK_MINUTES_RE.search(query)
    if m:
        return int(m.group(1))
    if _FALLBACK_DAY_RE.search(query):
        return 8 * 60 if _FALLBACK_FULL_DAY_RE.search(query) else 4 * 60
    return None


def _fallback_search_scope(query: str) -> str:
    """town / district / region, read off the query text."""
    if _FALLBACK_REGION_RE.search(query):
        return "region"
    if _FALLBACK_DISTRICT_RE.search(query):
        return "district"
    return "town"


def fallback_intent(query: str) -> IntentResult:
    """Model-free intent: everything the query text states, nothing invented.

    Deliberately conservative, because a wrong guess is worse than an honest
    default here:
      * categories_pos comes from the SAME deterministic keyword→category map
        retrieval uses (resolve.CATEGORY_SYNONYMS, inverted — see
        _fallback_categories).  «замки Гродно» now retrieves castles in
        degraded mode, not whatever bare keyword ILIKE happens to hit.
        categories_neg stays EMPTY — exclusion ("без замков") is a judgement
        call the text maps do not carry.
      * named_places come from the same token regex the Jev path uses, so
        they resolve through the DB exactly as before (must_visit_ids, and a
        town-only match becomes the geo anchor).
      * time_budget_minutes is kept only when the query states a duration.
      * intent_type / party_type / era_hint are the neutral defaults: nothing
        downstream branches on them (search_scope is the one that matters).
    """
    t0 = time.perf_counter()
    d = IntentDecision(
        # "vague" only for a query with no significant word at all ("?", "ааа");
        # otherwise "discovery", the neutral default nothing branches on.
        intent_type="vague" if not WORD_RE.search(query) else "discovery",
        categories_pos=_fallback_categories(query),
        categories_neg=[],
        keywords_pos=[],
        keywords_neg=[],
        named_places=_named_place_tokens(query),
        narrative=[],
        time_budget_minutes=_fallback_time_budget(query),
        era_hint="any",
        party_type="solo",
        search_scope=_fallback_search_scope(query),  # type: ignore[arg-type]
    )
    return IntentResult(
        decision=d,
        source="regex",  # no model was asked
        confidence=0.0,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        raw_response=None,
    )


def extract_intent(query: str) -> IntentResult:
    """Typed intent decision in one Jev call.

    Degrades to `fallback_intent` (source="regex") when OpenRouter cannot
    answer: no key, or an upstream failure.  With a key and a healthy
    upstream the result is exactly what Jev returned.
    """
    t0 = time.perf_counter()

    if not jev.available():
        log.warning(
            "intent: no OPENROUTER_API_KEY — deterministic fallback, map-based categories"
        )
        return fallback_intent(query)
    try:
        answers = jev.ask(query, _QUESTIONS)
    except jev.JevError as exc:
        log.warning("intent: Jev unavailable (%s) — deterministic fallback", exc)
        return fallback_intent(query)

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

    # Proper-noun candidates for must-visit resolution — same extraction the
    # degraded path uses (see _named_place_tokens for the verb caveat).
    named = _named_place_tokens(query)

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

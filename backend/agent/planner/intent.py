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
from dataclasses import dataclass, field

from .. import constants, jev
from ..models import GenerateReq, IntentDecision, IntentResult
from ..requirements import PartyComposition, Requirement, TripRequirements
from .preprocess import WORD_RE
from .resolve import CATEGORY_SYNONYMS, CATEGORY_SYNONYMS_EN

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
# Russian writes small durations as words ("на два часа"), English as words too
# ("for two hours"), so the number may be a digit OR a number word.
_HOUR_WORDS: dict[str, float] = {
    "один": 1, "одна": 1, "одного": 1, "одну": 1,
    "два": 2, "две": 2, "двоих": 2, "двух": 2,
    "три": 3, "трое": 3, "трёх": 3, "трех": 3,
    "четыре": 4, "четверо": 4, "четырёх": 4, "четырех": 4,
    "пять": 5, "пятеро": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "полтора": 1.5, "полторы": 1.5,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_HOUR_WORD_ALT = "|".join(
    _re.escape(w) for w in sorted(_HOUR_WORDS, key=len, reverse=True)
)

_FALLBACK_HOURS_RE = _re.compile(
    r"(\d{1,2})\s*(?:час\w*|ч(?![а-яё])|hours?\b|hrs?\b|h\b)", _re.I
)
_FALLBACK_HOURS_WORD_RE = _re.compile(
    r"\b(" + _HOUR_WORD_ALT + r")\s*(?:час\w*|hours?\b|hrs?\b)", _re.I
)
_FALLBACK_MINUTES_RE = _re.compile(
    r"(\d{1,3})\s*(?:минут\w*|мин(?![а-яё])|minutes?\b|mins?\b)", _re.I
)
# Whole phrases that name a duration without a number.
_FALLBACK_DAY_RE = _re.compile(
    r"(?:весь|целый|полный|на\s+весь)\s+день|сутк\w*|пол\s*дня|полдня|"
    r"\b(?:whole|full|all)\s+day|half\s+a?\s*day|"
    r"половин\w*\s+дня",
    _re.I,
)
_FALLBACK_FULL_DAY_RE = _re.compile(
    r"(?:весь|целый|полный|на\s+весь)\s+день|сутк\w*|"
    r"\b(?:whole|full|all)\s+day",
    _re.I,
)
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

# The same inversion, Russian AND English (spec 002 is RU/EN): the fallback has
# to read an English query without a model too.  RU forms are registered first,
# so a form both maps carry (e.g. "wc") keeps its Russian registration.
_SURFACE_FORMS: dict[str, str] = dict(_KEYWORD_TO_CATEGORY)
for _form, _cat in (
    (form, cat)
    for cat, forms in CATEGORY_SYNONYMS_EN.items()
    for form in forms
    if " " not in form
):
    _SURFACE_FORMS.setdefault(_form, _cat)


def _fallback_categories(query: str) -> list[str]:
    """Categories the query text itself states, read off the shared map.

    Deterministic word match on the lowercased query: «замкам» → "замок",
    «костёлам» → "костёл", «кофейне» → "кафе", "castles" → "замок".  A word
    the map does not know simply yields nothing, so a themed query with no
    category word returns an empty set — the honest answer, exactly like the
    model-free scope/time handling (nothing stated → nothing invented).
    """
    cats: list[str] = []
    seen: set[str] = set()
    for word in _re.findall(r"[а-яёa-z]+", query.lower()):
        cat = _SURFACE_FORMS.get(word)
        if cat and cat not in seen:
            seen.add(cat)
            cats.append(cat)
    return cats


def _fallback_time_budget(query: str) -> int | None:
    """Minutes of sightseeing the query itself budgets, or None.

    Only what the text states: "за 3 часа" → 180, "на два часа" → 120,
    "for two hours" → 120, "на 90 минут" → 90, "на полдня" → 240,
    "на весь день" → 480.  resolve() clamps the result to
    [MIN_BUDGET_MIN, MAX_BUDGET_MIN], so a wild number is bounded.
    """
    m = _FALLBACK_HOURS_RE.search(query)
    if m:
        return int(m.group(1)) * 60
    m = _FALLBACK_HOURS_WORD_RE.search(query)
    if m:
        return round(_HOUR_WORDS[m.group(1).lower()] * 60)
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


# ═════════════════════════════════════════════════════════════════════════════
# W2 — TripRequirements: the single interpretation entry point
# ═════════════════════════════════════════════════════════════════════════════
#
# `build_requirements(query, req)` is the ONE place that turns a tourist's free
# text plus the explicit UI filters into the frozen `TripRequirements` contract.
# It never touches the DB: named places and area slugs are names here, and the
# resolve stage grounds them.  Two readings are possible and both produce the
# same shape:
#
#   * LLM available — Jev's typed categories (planner/intent.extract_intent) are
#     merged over the deterministic reading, which supplies provenance spans and
#     the party/budget/area facts the typed model cannot give (a count, not
#     "family").  source="llm" (or "mixed" with UI filters).
#   * No key / upstream down — the deterministic reading alone.  source =
#     "fallback" (or "explicit" when only UI filters produced requirements).
#
# Rule: nothing is invented.  "двое детей" is a count of 2 with NO age; "без
# лестниц" is an unknown (there is no step-free graph to prove it), never a
# satisfied requirement.  The UI's explicit values win over any text guess.

# Everyday stops are services; everything else stated in the text is a theme.
_SERVICE_CODES = frozenset(constants.CONVENIENCE_CATEGORIES)

# A query-initial capitalised word is usually a verb ("Погулять", "Walk") — not
# a place.  These are filtered out of must-visit candidates by name; the DB
# would reject them anyway, but a clean name list is what the UI shows.
_RU_NAME_STOP = _PLACE_STOP_LIST | frozenset({
    "хочу", "хотелось", "погулять", "гулять", "пойдём", "пойдем", "посмотреть",
    "показать", "посетить", "найти", "сходить", "пройти", "прогуляться",
    "поехать", "съездить", "проехать", "составить", "подскажи", "расскажи",
    "организуй", "маршрут", "прогулка", "прогулку", "тур", "экскурсия",
    "экскурсию", "дай", "сделай", "можно",
})
_EN_NAME_STOP = frozenset({
    "walk", "walks", "wander", "stroll", "go", "visit", "visits", "see", "show",
    "find", "plan", "make", "want", "would", "like", "need", "needs", "please",
    "the", "a", "an", "i", "we", "my", "our", "and", "with", "for", "around",
    "in", "to", "from", "on", "at", "of", "old", "new", "day", "days", "trip",
    "route", "tour", "guide", "family", "kids", "children", "child", "nice",
    "good", "short", "long", "two", "three", "hour", "hours",
})

# Number words for a stated party size (RU has collective/case forms).
_COUNT_WORDS: dict[str, float] = {
    **_HOUR_WORDS,
    "двое": 2, "двоих": 2, "двумя": 2, "трое": 3, "тремя": 3, "троих": 3,
    "четверо": 4, "четырьмя": 4, "четверых": 4, "пятеро": 5, "шестеро": 6,
    "семеро": 7, "одним": 1, "одной": 1, "both": 2, "a": 1, "an": 1,
}
_COUNT_WORD_ALT = "|".join(
    _re.escape(w) for w in sorted(_COUNT_WORDS, key=len, reverse=True)
)

# Children / adults, RU and EN.  Only a COUNT is read; an age is a separate,
# explicitly-stated fact (see _AGE_RE) and is never derived from the count.
_RU_CHILD_RE = _re.compile(
    r"(?:с\s+)?(?P<num>\d{1,2}|" + _COUNT_WORD_ALT + r")\s*"
    r"(?:дет\w*|ребёнк\w*|ребенк\w*|малыш\w*|ребятишк\w*)",
    _re.I,
)
_EN_CHILD_RE = _re.compile(
    r"(?:with\s+)?(?P<num>\d{1,2}|" + _COUNT_WORD_ALT + r")\s*"
    r"(?:children|child|kids?|daughters?|sons?|babies|baby|infants?)",
    _re.I,
)
_RU_ADULT_RE = _re.compile(
    r"(?P<num>\d{1,2}|" + _COUNT_WORD_ALT + r")\s*(?:взросл\w*)", _re.I
)
_EN_ADULT_RE = _re.compile(
    r"(?P<num>\d{1,2}|" + _COUNT_WORD_ALT + r")\s*(?:adults?|grown[\s-]?ups?)", _re.I
)
_CHILD_WORD_RE = _re.compile(
    r"дет\w*|ребёнк\w*|ребенк\w*|малыш\w*|child\w*|kid\w*|daughters?|sons?|baby|infant",
    _re.I,
)
# "детям 5 и 8 лет", "6 years old".  Anchored on an age word, so it can never
# fire on "2 часа" / "3 stops".
_AGE_RE = _re.compile(
    r"((?:\d{1,2}\s*(?:,|и|and)?\s*){1,4})\s*"
    r"(?:лет\b|год\b|года\b|years?\s*old|y\.?o\.?)",
    _re.I,
)

# A stated party property that the text itself states (never inferred from the
# party size — a family of four is not automatically "with a stroller").
_MOBILITY_MARKERS: list[tuple[_re.Pattern, str]] = [
    (_re.compile(
        r"инвалидн\w*\s+коляск|кресл\w*[\s-]*коляск|wheelchair|"
        r"безбарьерн\w*|без\s+барьер\w*",
        _re.I,
    ), "wheelchair"),
    (_re.compile(r"коляск\w*|stroller|pram|pushchair|buggy", _re.I), "stroller"),
    (_re.compile(r"пожил\w*|престарел\w*|elderly|senior", _re.I), "elderly"),
]

# Things the request asks for that the system cannot represent or prove with
# the data it has.  These are surfaced to the user; they are NEVER satisfied
# requirements ("без лестниц" without a step-free graph).
_UNKNOWN_MARKERS: list[tuple[_re.Pattern, str]] = [
    (_re.compile(
        r"без\s+лестниц|без\s+ступен\w*|без\s+подъ[её]м\w*|безбарьерн\w*|"
        r"step[\s-]?free|without\s+stairs|no\s+stairs",
        _re.I,
    ), "step_free"),
    (_re.compile(
        r"доступн\w*\s+для\s+коляск|инвалидн\w*\s+коляск|"
        r"wheelchair[\s-]?accessible|accessib\w*\s+for\s+wheelchair",
        _re.I,
    ), "wheelchair_accessible"),
    (_re.compile(
        r"открыт\w*\s+сейчас|сейчас\s+открыт\w*|open\s+now|currently\s+open",
        _re.I,
    ), "opening_hours"),
]

# An obligation ("туалет обязательно", "must have a toilet") makes a service
# HARD; a wish ("кафе если по пути", "maybe a café") keeps it SOFT.
_OBLIGATION_RE = _re.compile(
    r"обязательн\w*|непременн\w*|необходим\w*|"
    r"\bнужен\b|\bнужна\b|\bнужно\b|\bнужны\b|"
    r"\bдолжен\b|\bдолжна\b|\bдолжно\b|\bдолжны\b|"
    r"\bmust\b|\brequired\b|definitely|\bshould\s+be\b|"
    r"нельзя\s+без",
    _re.I,
)
# A restriction removes a category from the route ("без замков", "not museums").
_AVOID_RE = _re.compile(
    r"\bбез\b|\bкроме\b|не\s+надо|не\s+хочу|не\s+нужн\w*|"
    r"\bavoid\b|\bexcept\b|\bexcluding\b|\bwithout\b|\bno\s+\w+",
    _re.I,
)

# A verified area the request is restricted to.  Slugs come from ONE controlled
# table (no areas DB exists yet); "старый город" must bind to a known area, not
# to the adjective "старый" and a random radius (spec §4.3).
_AREA_PATTERNS: list[tuple[str, _re.Pattern]] = [
    (
        "grodno-old-town",
        _re.compile(
            r"стар\w*\s+(?:город\w*|гродн\w*)|"
            r"old\s+(?:town|grodno)|historic\s+(?:centre|center)",
            _re.I,
        ),
    ),
]

_TERM_RE = _re.compile(r"[а-яёa-z]+")


@dataclass
class _Reading:
    """One reading of the query text — the deterministic parse plus, when the
    model is available, the categories Jev typed on top of it."""

    source: str
    requirements: list[Requirement] = field(default_factory=list)
    adults: int | None = None
    children: int | None = None
    children_ages: list[int] = field(default_factory=list)
    mobility: list[str] = field(default_factory=list)
    time_budget: int | None = None
    areas: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)


def _clause_containing(query: str, idx: int) -> str:
    """The comma/sentence fragment that contains `idx` — the provenance span.

    "туалет обязательно" is a fragment of "…, туалет обязательно, …", and that
    fragment — not the whole query — is what `Requirement.text` records.
    """
    delims = ".,;:!?—–\n"
    start = 0
    for i in range(idx - 1, -1, -1):
        if query[i] in delims:
            start = i + 1
            break
    end = len(query)
    for i in range(idx, len(query)):
        if query[i] in delims:
            end = i
            break
    return query[start:end].strip()


def _iter_terms(query: str):
    """Yield (category, start, end) for every taxonomy word in the query."""
    for m in _TERM_RE.finditer(query.lower()):
        cat = _SURFACE_FORMS.get(m.group(0))
        if cat is not None:
            yield cat, m.start(), m.end()


def _classify_term(cat: str, clause: str) -> tuple[str, str]:
    """(kind, strength) for one stated category, judged from its own fragment."""
    low = clause.lower()
    if _AVOID_RE.search(low):
        return "avoid", "hard"
    if cat in _SERVICE_CODES:
        return "service", ("hard" if _OBLIGATION_RE.search(low) else "soft")
    return "interest", "soft"


def _text_requirements(query: str) -> list[Requirement]:
    """Requirements the text itself states, each with its provenance span."""
    reqs: list[Requirement] = []
    seen: set[tuple[str, str | None]] = set()
    for cat, start, _end in _iter_terms(query):
        kind, strength = _classify_term(cat, _clause_containing(query, start))
        key = (kind, cat)
        if key in seen:
            continue
        seen.add(key)
        reqs.append(
            Requirement(
                kind=kind,  # type: ignore[arg-type]
                strength=strength,  # type: ignore[arg-type]
                code=cat,
                label=cat,
                text=_clause_containing(query, start) or None,
                source="text",
            )
        )
    return reqs


def _count_value(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    value = _COUNT_WORDS.get(token)
    return int(value) if value is not None else None


def _match_count(query: str, rx: _re.Pattern) -> int | None:
    m = rx.search(query)
    if not m:
        return None
    return _count_value(m.group("num"))


def _children_ages(query: str) -> list[int]:
    """Ages the user actually named (0 < age ≤ 17) — never a guess."""
    if not _CHILD_WORD_RE.search(query):
        return []
    out: list[int] = []
    for m in _AGE_RE.finditer(query):
        for token in _re.findall(r"\d{1,2}", m.group(1)):
            age = int(token)
            if 0 < age <= 17 and age not in out:
                out.append(age)
    return out


def _mobility_from_text(query: str) -> list[str]:
    out: list[str] = []
    for rx, code in _MOBILITY_MARKERS:
        if rx.search(query) and code not in out:
            out.append(code)
    return out


def _unknowns_from_text(query: str) -> list[str]:
    out: list[str] = []
    for rx, code in _UNKNOWN_MARKERS:
        if rx.search(query) and code not in out:
            out.append(code)
    return out


def _named_tokens(query: str) -> list[str]:
    """Proper-noun candidates, RU and EN, minus query verbs / area names.

    RU uses the same token regex the Jev path uses (`_named_place_tokens`), then
    drops sentence-initial verbs; EN adds capitalised Latin words minus common
    query words. Debate about a token's identity is not settled here — the
    resolve stage matches it against the DB.
    """
    out: list[str] = []
    seen: set[str] = set()
    for token in _named_place_tokens(query):
        low = token.lower()
        # A taxonomy word ("Замки") or a query verb ("Погулять") is not a place.
        if low in _RU_NAME_STOP or low in _SURFACE_FORMS or low in seen:
            continue
        seen.add(low)
        out.append(token)
    for token in _re.findall(r"\b[A-Z][a-z]{2,}\b", query):
        low = token.lower()
        if low in _EN_NAME_STOP or low in _SURFACE_FORMS or low in seen:
            continue
        seen.add(low)
        out.append(token)
    return out


def _is_grodno(text: str) -> bool:
    """True when a token/query names Grodno in either script."""
    low = text.lower()
    return "гродн" in low or "grodn" in low


def _areas_from_text(query: str) -> list[str]:
    """Controlled area slugs the text names — never a bare adjective.

    "старый город" binds to Grodno's old town only when the query is about
    Grodno (or names no other town): «Лида, замок и старый город» must not be
    restricted to a Grodno area just because it contains the words "старый
    город".
    """
    other_towns = [n for n in _named_tokens(query) if not _is_grodno(n)]
    out: list[str] = []
    for slug, rx in _AREA_PATTERNS:
        if not rx.search(query) or slug in out:
            continue
        if slug == "grodno-old-town" and other_towns and not _is_grodno(query):
            continue
        out.append(slug)
    return out


def _party_from_text(query: str) -> tuple[int | None, int | None, list[int], list[str]]:
    children = _match_count(query, _RU_CHILD_RE)
    if children is None:
        children = _match_count(query, _EN_CHILD_RE)
    if children is not None and not (0 <= children <= 20):
        children = None
    ages = _children_ages(query)
    if children is None and ages:
        # "с детьми 5 и 9 лет" enumerates the children — the count is the number
        # of ages the user stated, not a guess.
        children = len(ages)
    adults = _match_count(query, _RU_ADULT_RE)
    if adults is None:
        adults = _match_count(query, _EN_ADULT_RE)
    if adults is not None and not (0 <= adults <= 50):
        adults = None
    return (
        adults,
        children,
        ages,
        _mobility_from_text(query),
    )


def _fallback_reading(query: str, _locale: str) -> _Reading:
    """Everything the query text states, without a model — nothing invented."""
    adults, children, ages, mobility = _party_from_text(query)
    return _Reading(
        source="fallback",
        requirements=_text_requirements(query),
        adults=adults,
        children=children,
        children_ages=ages,
        mobility=mobility,
        time_budget=_fallback_time_budget(query),
        areas=_areas_from_text(query),
        unknowns=_unknowns_from_text(query),
    )


def _clause_for_category(query: str, cat: str) -> str:
    for found, start, _end in _iter_terms(query):
        if found == cat:
            return _clause_containing(query, start)
    return ""


def _llm_reading(query: str, locale: str) -> _Reading:
    """The deterministic reading plus the categories Jev typed on top.

    The typed model decides *which* categories a query is about (including
    phrasings the word map misses); the deterministic pass keeps provenance and
    the party/budget/area facts that a typed choice cannot express.  A Jev
    outage degrades to the plain fallback — never to an exception.
    """
    base = _fallback_reading(query, locale)
    try:
        intent = extract_intent(query)
    except jev.JevError:
        log.warning("requirements: Jev unavailable — deterministic reading")
        return base
    if intent.source != "jev":
        # extract_intent degrades internally on an upstream outage; that is the
        # same situation as no key — the requirements come from the text alone.
        log.info("requirements: intent source=%s — deterministic reading", intent.source)
        return base

    d = intent.decision
    conf = max(0.0, min(1.0, round(intent.confidence, 3)))
    reqs = list(base.requirements)
    seen = {(r.kind, r.code) for r in reqs}

    for cat in d.categories_pos:
        kind = "service" if cat in _SERVICE_CODES else "interest"
        clause = _clause_for_category(query, cat)
        if kind == "service":
            strength = "hard" if _OBLIGATION_RE.search(clause.lower()) else "soft"
        else:
            strength = "soft"
        if (kind, cat) in seen:
            continue
        seen.add((kind, cat))
        reqs.append(
            Requirement(
                kind=kind,  # type: ignore[arg-type]
                strength=strength,  # type: ignore[arg-type]
                code=cat,
                label=cat,
                text=clause or None,
                source="text",
                confidence=conf,
            )
        )

    for cat in d.categories_neg:
        if ("avoid", cat) in seen:
            continue
        seen.add(("avoid", cat))
        clause = _clause_for_category(query, cat)
        reqs.append(
            Requirement(
                kind="avoid",
                strength="hard",
                code=cat,
                label=cat,
                text=clause or None,
                source="text",
                confidence=conf,
            )
        )

    budget = base.time_budget if base.time_budget is not None else d.time_budget_minutes
    return _Reading(
        source="llm",
        requirements=reqs,
        adults=base.adults,
        children=base.children,
        children_ages=base.children_ages,
        mobility=base.mobility,
        time_budget=budget,
        areas=base.areas,
        unknowns=base.unknowns,
    )


def _read_text(query: str, locale: str) -> _Reading:
    """LLM reading when OpenRouter can answer, deterministic reading otherwise."""
    if jev.available():
        return _llm_reading(query, locale)
    log.info("requirements: no OPENROUTER_API_KEY — deterministic reading")
    return _fallback_reading(query, locale)


def _ui_requirements(req: GenerateReq) -> list[Requirement]:
    """Requirements the tourist set with a visible control (source="ui")."""
    out: list[Requirement] = []
    for code in req.hard_services:
        out.append(
            Requirement(kind="service", strength="hard", code=code, label=code, source="ui")
        )
    for code in req.interests:
        out.append(
            Requirement(kind="interest", strength="soft", code=code, label=code, source="ui")
        )
    for code in req.avoid:
        out.append(
            Requirement(kind="avoid", strength="hard", code=code, label=code, source="ui")
        )
    return out


def _ui_used(req: GenerateReq, ui_reqs: list[Requirement]) -> bool:
    """True when an explicit control contributed anything to the request.

    `time_budget_minutes == 0` is the selector's "без ограничения" value, the
    same as an absent field; it does not count as an explicit choice.
    """
    return bool(ui_reqs) or (
        req.party_adults is not None
        or req.party_children is not None
        or bool(req.party_children_ages)
        or bool(req.mobility)
        or req.time_budget_minutes not in (None, 0)
    )


def _merge_requirements(
    ui_reqs: list[Requirement], text_reqs: list[Requirement]
) -> tuple[list[Requirement], set[tuple[str, str | None]]]:
    """UI requirements first (they win); a text reading cannot duplicate them."""
    merged = list(ui_reqs)
    claimed = {(r.kind, r.code or r.name) for r in ui_reqs}
    for r in text_reqs:
        key = (r.kind, r.code or r.name)
        if key in claimed:
            continue
        claimed.add(key)
        merged.append(r)
    return merged, claimed


def build_requirements(query: str, req: GenerateReq) -> TripRequirements:
    """Interpret one request into the frozen `TripRequirements` contract.

    Single entry point for "what did the tourist ask for".  Works RU and EN,
    with the LLM present and in the degraded no-key path; explicit UI filters
    always win over a text reading, and nothing the data cannot prove is
    presented as satisfied (it goes to `unknowns` instead).
    """
    locale = req.locale
    reading = _read_text(query, locale)

    # ── Requirements: UI first (it wins), then the text reading ──
    ui_reqs = _ui_requirements(req)
    requirements, claimed = _merge_requirements(ui_reqs, reading.requirements)

    # Named places the user asked for: names now, grounded to IDs in resolve().
    for name in _named_tokens(query):
        key = ("must_visit", name)
        if key in claimed:
            continue
        claimed.add(key)
        requirements.append(
            Requirement(kind="must_visit", name=name, label=name, text=name, source="text")
        )

    # ── Party: explicit values win; ages are never invented ──
    children = req.party_children if req.party_children is not None else reading.children
    adults = req.party_adults if req.party_adults is not None else reading.adults
    ages = (
        list(req.party_children_ages)
        if req.party_children_ages
        else list(reading.children_ages)
    )
    mobility: list[str] = []
    for code in list(req.mobility) + list(reading.mobility):
        if code and code not in mobility:
            mobility.append(code)

    # ── Budget: the UI selector wins, including "0 = без ограничения" ──
    if req.time_budget_minutes is not None:
        budget = req.time_budget_minutes or None
    else:
        budget = reading.time_budget
    if budget is not None:
        budget = max(constants.MIN_BUDGET_MIN, min(budget, constants.MAX_BUDGET_MIN))

    # ── Unknowns: what cannot be proven is named, never promised ──
    unknowns = list(reading.unknowns)
    if "wheelchair" in mobility and "wheelchair_accessible" not in unknowns:
        unknowns.append("wheelchair_accessible")

    # ── How the requirements were obtained ──
    ui_used = _ui_used(req, ui_reqs)
    if reading.source == "llm" and ui_used:
        source: str = "mixed"
    elif reading.source == "llm":
        source = "llm"
    elif ui_used:
        source = "explicit"
    else:
        source = "fallback"

    return TripRequirements(
        locale=locale,
        raw_query=query,
        party=PartyComposition(
            adults=adults,
            children=children,
            children_ages=ages,
            mobility=mobility,
        ),
        budget_minutes=budget,
        costing=req.profile,
        origin_lat=req.origin.lat if req.origin is not None else None,
        origin_lon=req.origin.lon if req.origin is not None else None,
        areas=reading.areas,
        result_mode=req.result_mode,
        round_trip=req.round_trip,
        requirements=requirements,
        unknowns=unknowns,
        source=source,  # type: ignore[arg-type]
    )

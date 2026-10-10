"""Step 1 — Intent, and the TripRequirements interpretation entry point."""

from __future__ import annotations

import logging as _logging
import re as _re
import time
from dataclasses import dataclass, field

from contracts.planner import GenerateReq, IntentDecision, IntentResult
from domain import constants
from domain.requirements import PartyComposition, Requirement, TripRequirements
from infra import trace

from . import interpret_cache
from .preprocess import WORD_RE
from .resolve import CATEGORY_SYNONYMS, CATEGORY_SYNONYMS_EN

log = _logging.getLogger(__name__)

_PLACE_STOP_LIST: frozenset[str] = frozenset({
    "гродненская", "гродненской", "гродненскому", "гродненском",
    "область", "области", "областью", "областях",
})


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
_FALLBACK_REGION_RE = _re.compile(
    r"област\w*|регион\w*|кра[йея]\b|по\s+все[йм][\w\s]*|всю\s+область",
    _re.I,
)
_FALLBACK_DISTRICT_RE = _re.compile(r"район\w*", _re.I)


def _named_place_tokens(query: str) -> list[str]:
    """Proper-noun candidates for must-visit resolution: capitalised words inside
    the Russian query (works for toponyms and place names).
    """
    tokens = _re.findall(r"[А-ЯЁ][а-яё\-]{2,}", query)
    return [t for t in tokens if t.lower() not in _PLACE_STOP_LIST]


_KEYWORD_TO_CATEGORY: dict[str, str] = {
    form: cat
    for cat, forms in CATEGORY_SYNONYMS.items()
    for form in forms
    if " " not in form
}

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

    A word the map does not know yields nothing — nothing stated, nothing invented.
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
    """Minutes of sightseeing the query itself budgets, or None."""
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

    Deliberately conservative, because a wrong guess is worse than an honest default here.
    """
    t0 = time.perf_counter()
    d = IntentDecision(
        intent_type="vague" if not WORD_RE.search(query) else "discovery",
        categories_pos=_fallback_categories(query),  # type: ignore[arg-type]
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
        source="regex",
        confidence=0.0,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        raw_response=None,
    )


def extract_intent(query: str) -> IntentResult:
    """Deterministic intent decision in one call (source="regex")."""
    return fallback_intent(query)


def intent_from_requirements(
    requirements: TripRequirements, query: str
) -> IntentResult:
    """Derive the ``IntentResult`` that ``resolve()`` consumes from a contract."""
    cats_pos: list[str] = []
    for code in (
        requirements.interest_codes()
        + requirements.soft_service_codes()
        + requirements.hard_service_codes()
    ):
        if code and code in constants.CATEGORIES and code not in cats_pos:
            cats_pos.append(code)
    cats_neg = [
        c for c in requirements.avoid_codes()
        if c in constants.CATEGORIES and c not in cats_pos
    ]

    named = [r.name for r in requirements.of_kind("must_visit") if r.name]
    if not named:
        named = _named_tokens(query)

    decision = IntentDecision(
        intent_type="vague" if not WORD_RE.search(query) else "discovery",
        categories_pos=cats_pos,  # type: ignore[arg-type]
        categories_neg=cats_neg,  # type: ignore[arg-type]
        keywords_pos=[],
        keywords_neg=[],
        named_places=named,
        narrative=[],
        time_budget_minutes=requirements.budget_minutes,
        era_hint="any",
        party_type="solo",
        search_scope=_fallback_search_scope(query),  # type: ignore[arg-type]
    )
    return IntentResult(
        decision=decision,
        source="agent" if requirements.source in ("llm", "mixed") else "fallback",
        confidence=0.0,
        latency_ms=0,
        raw_response=None,
    )


_SERVICE_CODES = frozenset(constants.CONVENIENCE_CATEGORIES)

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

_COUNT_WORDS: dict[str, float] = {
    **_HOUR_WORDS,
    "двое": 2, "двоих": 2, "двумя": 2, "трое": 3, "тремя": 3, "троих": 3,
    "четверо": 4, "четырьмя": 4, "четверых": 4, "пятеро": 5, "шестеро": 6,
    "семеро": 7, "одним": 1, "одной": 1, "both": 2, "a": 1, "an": 1,
}
_COUNT_WORD_ALT = "|".join(
    _re.escape(w) for w in sorted(_COUNT_WORDS, key=len, reverse=True)
)

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
_AGE_RE = _re.compile(
    r"((?:\d{1,2}\s*(?:,|и|and)?\s*){1,4})\s*"
    r"(?:лет\b|год\b|года\b|years?\s*old|y\.?o\.?)",
    _re.I,
)

_MOBILITY_MARKERS: list[tuple[_re.Pattern, str]] = [
    (_re.compile(
        r"инвалидн\w*\s+коляск|кресл\w*[\s-]*коляск|wheelchair|"
        r"безбарьерн\w*|без\s+барьер\w*",
        _re.I,
    ), "wheelchair"),
    (_re.compile(r"коляск\w*|stroller|pram|pushchair|buggy", _re.I), "stroller"),
    (_re.compile(r"пожил\w*|престарел\w*|elderly|senior", _re.I), "elderly"),
]

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

_OBLIGATION_RE = _re.compile(
    r"обязательн\w*|непременн\w*|необходим\w*|"
    r"\bнужен\b|\bнужна\b|\bнужно\b|\bнужны\b|"
    r"\bдолжен\b|\bдолжна\b|\bдолжно\b|\bдолжны\b|"
    r"\bmust\b|\brequired\b|definitely|\bshould\s+be\b|"
    r"нельзя\s+без",
    _re.I,
)
_AVOID_RE = _re.compile(
    r"\bбез\b|\bкроме\b|не\s+надо|не\s+хочу|не\s+нужн\w*|"
    r"\bavoid\b|\bexcept\b|\bexcluding\b|\bwithout\b|\bno\s+\w+",
    _re.I,
)

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
    """One reading of the query text — the deterministic parse of it."""

    source: str
    requirements: list[Requirement] = field(default_factory=list)
    adults: int | None = None
    children: int | None = None
    children_ages: list[int] = field(default_factory=list)
    mobility: list[str] = field(default_factory=list)
    time_budget: int | None = None
    areas: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    outside_coverage: list[str] = field(default_factory=list)


def _clause_containing(query: str, idx: int) -> str:
    """The comma/sentence fragment that contains `idx` — the provenance span."""
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

    Identity is not settled here — the resolve stage matches tokens against the DB.
    """
    out: list[str] = []
    seen: set[str] = set()
    for token in _named_place_tokens(query):
        low = token.lower()
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
    """Controlled area slugs the text names — never a bare adjective."""
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


def _read_text(query: str, locale: str) -> _Reading:
    """The deterministic reading of the query text.

    The no-model path, and what the agent path degrades to.
    """
    return _fallback_reading(query, locale)


def _interpret_cache_key(
    query: str, req: GenerateReq
) -> tuple[str | None, str]:
    """None when the agent cannot run at all; the prompt is hashed into the key, so
    editing the instructions invalidates every entry.
    """
    try:
        from . import agent_interpret, interpret_cache

        if not agent_interpret.available():
            return None, ""
        instructions = agent_interpret._instructions(agent_interpret._ui_note(req))
        return (
            interpret_cache.interpret_key(
                query, req, instructions, agent_interpret._model_name()
            ),
            interpret_cache.prompt_hash(instructions),
        )
    except Exception as exc:
        log.warning("requirements: cache key unavailable (%s)", exc)
        return None, ""


def _agent_contract(
    query: str, req: GenerateReq, db, wall_clock_s: float | None = None
) -> TripRequirements | None:
    """The interpretation agent's contract, or None when it cannot be trusted.

    Last-resort guard: an unexpected error can never turn a route request into a 500.
    """
    from . import agent_interpret
    try:
        return agent_interpret.interpret_with_agent(
            query, req, db=db, wall_clock_s=wall_clock_s
        )
    except Exception as exc:  # pragma: no cover — defensive; the layer is guarded
        log.warning("requirements: agent layer failed (%s) — deterministic reading", exc)
        return None


def _territory_slug(name: str) -> str | None:
    """A territory names where to look, not what to visit, so it is not a
    requirement and not an area either.
    """
    from domain import areas as areas_mod

    return areas_mod.resolve_area((name or "").strip())


def mark_out_of_coverage(contract: TripRequirements, names: list[str]) -> None:
    """Marks each `hard`; a refusal is never invented here — the verifier reports
    what the contract keeps.
    """
    from domain.requirements import REASON_MUST_VISIT_OUTSIDE

    by_name = {
        (r.name or "").strip().lower(): r
        for r in contract.requirements
        if r.kind == "must_visit"
    }
    for name in names:
        key = name.strip().lower()
        if not key:
            continue
        r = by_name.get(key)
        if r is None:
            r = Requirement(
                kind="must_visit", name=name, label=name, text=name, source="text"
            )
            contract.requirements.append(r)
            by_name[key] = r
        r.strength = "hard"
        r.reason = REASON_MUST_VISIT_OUTSIDE
        r.place_id = None


def _is_fragment_of(name: str, known: list[str]) -> bool:
    """Is a named token a piece of a longer place name we already have?"""
    norm = name.strip().lower()
    if not norm:
        return False
    return any(
        norm != other.strip().lower() and norm in other.strip().lower().split()
        for other in known
        if other
    )


def _finalize_agent_contract(
    contract: TripRequirements, query: str, req: GenerateReq
) -> TripRequirements:
    """Top up an agent contract with the deterministic facts it must not omit."""
    contract.requirements, claimed = _merge_requirements(
        _ui_requirements(req), contract.requirements
    )
    ui_positive = {
        r.code for r in _ui_requirements(req) if r.code and r.kind in ("interest", "service")
    }
    contract.requirements = [
        r for r in contract.requirements
        if not (r.kind == "avoid" and r.code in ui_positive)
    ]

    for r in _read_text(query, req.locale).requirements:
        if r.kind not in ("service", "interest", "avoid"):
            continue
        key = (r.kind, r.code or r.name)
        if key in claimed:
            continue
        claimed.add(key)
        contract.requirements.append(r)

    text_mandatory = {
        (r.kind, r.code or r.name)
        for r in _read_text(query, req.locale).requirements
        if r.strength == "hard"
    }
    for r in contract.requirements:
        if r.strength == "soft" and (r.kind, r.code or r.name) in text_mandatory:
            r.strength = "hard"

    if req.party_children is not None:
        contract.party.children = req.party_children
    if req.party_adults is not None:
        contract.party.adults = req.party_adults
    if req.party_children_ages:
        contract.party.children_ages = list(req.party_children_ages)
    for code in req.mobility:
        if code and code not in contract.party.mobility:
            contract.party.mobility.append(code)
    if req.time_budget_minutes is not None:
        contract.budget_minutes = req.time_budget_minutes or None

    for name in _named_tokens(query):
        if _territory_slug(name):
            continue
        if _is_fragment_of(
            name,
            [r.name for r in contract.requirements if r.kind == "must_visit" and r.name],
        ):
            continue
        key = ("must_visit", name)
        if key in claimed:
            continue
        claimed.add(key)
        contract.requirements.append(
            Requirement(
                kind="must_visit", name=name, label=name, text=name, source="text",
            )
        )
    for code in _unknowns_from_text(query):
        if code not in contract.unknowns:
            contract.unknowns.append(code)
    if "wheelchair" in contract.party.mobility and "wheelchair_accessible" not in contract.unknowns:
        contract.unknowns.append("wheelchair_accessible")
    if contract.budget_minutes is not None:
        contract.budget_minutes = max(
            constants.MIN_BUDGET_MIN, min(contract.budget_minutes, constants.MAX_BUDGET_MIN)
        )
    if contract.source == "llm" and _ui_used(req, [r for r in contract.requirements if r.source == "ui"]):
        contract.source = "mixed"
    return contract


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

    `time_budget_minutes == 0` means "без ограничения" and does not count as a choice.
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


def build_requirements(
    query: str, req: GenerateReq, *, db: object | None = None,
    wall_clock_s: float | None = None,
) -> TripRequirements:
    """Interpret one request into the frozen `TripRequirements` contract."""
    cache_key, prompt_hash = _interpret_cache_key(query, req)
    if cache_key is not None:
        cached = interpret_cache.INTERPRET_CACHE.get(cache_key)
        if cached is not None:
            log.info("requirements: cached reading (no model call)")
            trace.record("interpret · model", "skipped", cached=True)
            return cached.model_copy(deep=True)

    try:
        contract = _agent_contract(query, req, db, wall_clock_s)
    except Exception as exc:
        log.warning("requirements: agent raised (%s) — deterministic parse", exc)
        contract = None
    if contract is not None:
        log.info(
            "requirements: agent reading (source=%s, %d requirement(s))",
            contract.source, len(contract.requirements),
        )
        final = _finalize_agent_contract(contract, query, req)
        if cache_key is not None:
            interpret_cache.INTERPRET_CACHE.put(
                cache_key, final.model_copy(deep=True), prompt_hash
            )
        return final
    log.info("requirements: no agent reading — deterministic parse")
    return _deterministic_requirements(query, req)


def _deterministic_requirements(query: str, req: GenerateReq) -> TripRequirements:
    """The no-model reading of one request, as the frozen contract."""
    locale = req.locale
    reading = _read_text(query, locale)

    ui_reqs = _ui_requirements(req)
    requirements, claimed = _merge_requirements(ui_reqs, reading.requirements)

    for name in _named_tokens(query):
        if _territory_slug(name):
            continue
        if _is_fragment_of(
            name,
            [r.name for r in requirements if r.kind == "must_visit" and r.name],
        ):
            continue
        key = ("must_visit", name)
        if key in claimed:
            continue
        claimed.add(key)
        requirements.append(
            Requirement(kind="must_visit", name=name, label=name, text=name, source="text")
        )

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

    if req.time_budget_minutes is not None:
        budget = req.time_budget_minutes or None
    else:
        budget = reading.time_budget
    if budget is not None:
        budget = max(constants.MIN_BUDGET_MIN, min(budget, constants.MAX_BUDGET_MIN))

    unknowns = list(reading.unknowns)
    if "wheelchair" in mobility and "wheelchair_accessible" not in unknowns:
        unknowns.append("wheelchair_accessible")

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

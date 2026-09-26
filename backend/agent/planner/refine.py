"""Route refinement: interpret a delta instruction, honestly.

A refinement turn does not re-plan the trip.  The frontend sends the route as it
stands (`RouteContext.base_points`), the stops the tourist deleted
(`excluded_ids`) and the delta instruction ("добавь кофейню", "отсортируй по
времени посещения").  This module turns that instruction into ONE typed
operation and the pipeline applies exactly that operation to the previous route.

Two rules from the product contract drive everything here:

* A refinement must KEEP the base route's stops unless the instruction
  explicitly adds, removes or excludes something.  A refinement that we cannot
  carry out is refused honestly (a machine `reason_code` plus a short human
  text) and the previous route is returned untouched — never silently replaced
  with a freshly planned, unrelated route.
* Stop identity is stable: every operation works on the `Candidate` objects the
  route already has (id/name/coordinates from the previous turn), never on a
  re-derived set.

The operation vocabulary is deliberately small and typed:

    none         no delta instruction — keep the route as it is
    add          the instruction names categories/places to ADD
    remove       the instruction names something to REMOVE
    reorder      the instruction asks to reorder the EXISTING stops by an
                 attribute (visit time, or distance from the route start)
    unsupported  the instruction names an operation this planner does not do

`unsupported` carries a machine `reason_code`; the localization happens in the
API/UI layer, never here.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

from .. import taxonomy
from ..models import Candidate, LatLon
from .cost import visit_time_minutes
from .resolve import CATEGORY_SYNONYMS, CATEGORY_SYNONYMS_EN

# ── Machine reason codes (localized by the API layer, never here) ────────────

# An operation we recognise but cannot perform ("сделай маршрут короче").
REFINEMENT_UNSUPPORTED = "refinement_unsupported"
# A non-empty instruction we could not turn into any operation at all.
REFINEMENT_UNRECOGNIZED = "refinement_unrecognized"
# A reorder request that names no attribute we can sort by.
REFINEMENT_REORDER_ATTRIBUTE_MISSING = "refinement_reorder_attribute_missing"

RefinementOperation = Literal["none", "add", "remove", "reorder", "unsupported"]
ReorderBy = Literal["visit_minutes", "distance"]

# Short human texts, keyed by reason code.  The contract is the code; this is
# the fallback wording the API shows when the UI has no localization for it.
REASON_TEXT: dict[str, str] = {
    REFINEMENT_UNSUPPORTED: (
        "Это изменение маршрута пока не поддерживается — оставили маршрут как есть."
    ),
    REFINEMENT_UNRECOGNIZED: (
        "Не поняли, что именно изменить — оставили маршрут как есть."
    ),
    REFINEMENT_REORDER_ATTRIBUTE_MISSING: (
        "Не указано, по какому признаку упорядочить остановки — оставили маршрут как есть."
    ),
}

# ── Instruction verbs ───────────────────────────────────────────────────────
# Word-boundary regexes; Russian \b works on Cyrillic in Python 3 (\w is
# unicode-aware).  Only the *verb* is matched here — the attribute (what to
# sort by, what to add) is matched separately so "сделай короче" is not
# mistaken for "сначала самые короткие".

_ADD_RE = re.compile(
    r"(?<![а-яa-z])(?:добав\w*|добавить|включ\w*|подключ\w*|"
    r"нужен|нужна|нужны|нужно|хочу\s+ещ[её]|ещ[её]\b|"
    r"add|include|append)(?![а-яa-z])",
    re.IGNORECASE,
)
_REMOVE_RE = re.compile(
    r"(?<![а-яa-z])(?:убер\w*|удал\w*|исключ\w*|"
    r"без\b|кроме\b|не\s+хочу|не\s+надо|"
    r"remove|delete|exclude|drop)(?![а-яa-z])",
    re.IGNORECASE,
)
_REORDER_RE = re.compile(
    r"(?<![а-яa-z])(?:отсорт\w*|сортир\w*|упорядоч\w*|переупорядоч\w*|"
    r"перестав\w*|располож\w*|порядок|последовательност\w*|"
    r"сначала|начни\s+с\b|"
    r"sort|order|rearrange|reorder)(?![а-яa-z])",
    re.IGNORECASE,
)

# Attribute: visit time ("по времени посещения", "сначала самые длинные").
_VISIT_ATTR_RE = re.compile(
    r"(?<![а-яa-z])(?:врем\w*|длительн\w*|минут\w*|"
    r"длинн\w*|дольше|долг\w*|коротк\w*|короч\w*|"
    r"time|duration|minutes)(?![а-яa-z])",
    re.IGNORECASE,
)
# Attribute: distance from the route start ("по расстоянию", "от старта").
_DIST_ATTR_RE = re.compile(
    r"(?<![а-яa-z])(?:расстоян\w*|удал[её]нн\w*|дальност\w*|"
    r"от\s+старта|от\s+начала|ближайш\w*|ближе|"
    r"distance|nearest|from\s+the\s+start)(?![а-яa-z])",
    re.IGNORECASE,
)
# "сначала самые длинные" => longest visits first.
_DESC_RE = re.compile(
    r"(?<![а-яa-z])(?:длинн\w*|дольше|долг\w*|по\s+убыванию|"
    r"больше\s+времени|longest|descending|desc)(?![а-яa-z])",
    re.IGNORECASE,
)
_ASC_RE = re.compile(
    r"(?<![а-яa-z])(?:коротк\w*|короч\w*|по\s+возрастанию|"
    r"меньше\s+времени|shortest|ascending|asc)(?![а-яa-z])",
    re.IGNORECASE,
)

# An operation verb we recognise but do not implement: shorten, optimise,
# rebuild differently, translate, ...
_UNSUPPORTED_OP_RE = re.compile(
    r"(?<![а-яa-z])(?:сделай|сделать|построй|построить|перестрой\w*|"
    r"пересобер\w*|оптимизир\w*|сократ\w*|уменьш\w*|увелич\w*|"
    r"больше\s+времени|меньше\s+времени|удели\s+времени|"
    r"измени\w*|изменить|поменяй|поменять|замени\w*|переведи|перевести|"
    r"другой\s+маршрут|новый\s+маршрут|"
    r"optimize|shorten|translate|rebuild|more\s+time|less\s+time)(?![а-яa-z])",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)

# Words that carry no meaning for "what to remove" extraction.
_STOPWORDS = frozenset(
    "и в во на с со по для от до из за у к о об это тот этот эти пожалуйста "
    "маршрут маршруте остановку остановки точки точку "
    "лишнее всё все все всё-таки только ещё еще".split()
)


def _norm(text: str) -> str:
    return (text or "").strip().lower().replace("ё", "е")


def _has(pattern: re.Pattern[str], text: str) -> bool:
    return bool(pattern.search(text or ""))


# ── The typed operation ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class RefinementPlan:
    """One typed refinement operation, ready for the pipeline to apply.

    ``supported`` is the honest switch: when it is False the only thing the
    pipeline may do is keep the previous route and report ``reason_code``.
    """

    operation: RefinementOperation = "none"

    # Categories (canonical codes) and named places the instruction asks to ADD.
    add_categories: tuple[str, ...] = ()
    add_names: tuple[str, ...] = ()

    # Categories the instruction asks to EXCLUDE ("без музеев", "исключи кафе"):
    # stops of these categories must be removed from the previous route.
    exclude_categories: tuple[str, ...] = ()

    # Named places the instruction asks to REMOVE (plus anything in
    # `excluded_ids`, handled by the pipeline).
    remove_names: tuple[str, ...] = ()

    # Reorder target and direction.
    reorder_by: ReorderBy | None = None
    descending: bool = False

    # Honesty channel.
    reason_code: str | None = None
    detail: str | None = None

    @property
    def supported(self) -> bool:
        return self.operation != "unsupported"

    @property
    def cats(self) -> tuple[str, ...]:
        """Every category code named by the instruction (add + exclude)."""
        return self.add_categories + self.exclude_categories

    def is_noop(self) -> bool:
        return self.operation == "none"


def _detected_categories(text: str) -> tuple[str, ...]:
    """Canonical category codes named in the instruction, in taxonomy order.

    Uses the same surface-form maps retrieval and intent extraction use, then
    falls back to the canonical taxonomy resolver — one taxonomy, no third
    private word list.
    """
    norm = _norm(text)
    if not norm:
        return ()

    hits: set[str] = set()
    for code, synonyms in (*CATEGORY_SYNONYMS.items(), *CATEGORY_SYNONYMS_EN.items()):
        for syn in synonyms:
            if len(syn) < 3:
                continue
            if syn in norm:
                hits.add(code)
                break

    for token in _TOKEN_RE.findall(norm):
        if len(token) < 3:
            continue
        code = taxonomy.resolve_code(token)
        if code:
            hits.add(code)

    # Keep the taxonomy's own order so the result is deterministic.
    return tuple(c for c in taxonomy.all_codes() if c in hits)


def _removed_names(text: str) -> tuple[str, ...]:
    """Tokens the instruction asks to remove ("убери форт" → ("форт",))."""
    norm = _norm(text)
    if not _REMOVE_RE.search(norm):
        return ()
    # Everything after the first remove verb, minus stopwords and category
    # words (a category removal is expressed through the category, not a name).
    match = _REMOVE_RE.search(norm)
    tail = norm[match.end():] if match else norm
    cats = set(_detected_categories(text))
    out: list[str] = []
    for token in _TOKEN_RE.findall(tail):
        if len(token) < 3 or token in _STOPWORDS:
            continue
        if _has(_ADD_RE, token) or _has(_REMOVE_RE, token):
            continue
        code = taxonomy.resolve_code(token)
        if code or token in cats:
            continue
        out.append(token)
    return tuple(dict.fromkeys(out))


def _category_position(norm: str, code: str) -> int:
    """Earliest character index of any surface form of `code` in `norm`."""
    best = len(norm) + 1
    syns = (*CATEGORY_SYNONYMS.get(code, ()), *CATEGORY_SYNONYMS_EN.get(code, ()), code)
    for syn in syns:
        if len(syn) < 3:
            continue
        pos = norm.find(syn)
        if pos >= 0:
            best = min(best, pos)
    return 0 if best > len(norm) else best


def _classify_categories(
    text: str, codes: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split named categories into (to-add, to-exclude) by the nearest verb.

    "добавь кафе и убери музеи" → add ("кафе",), exclude ("музей",).  A
    category whose nearest preceding verb is a removal is an EXCLUSION and is
    never added.
    """
    if not codes:
        return ((), ())
    norm = _norm(text)
    verbs: list[tuple[int, str]] = []
    for match in _ADD_RE.finditer(norm):
        verbs.append((match.start(), "add"))
    for match in _REMOVE_RE.finditer(norm):
        verbs.append((match.start(), "remove"))
    verbs.sort()

    adds: list[str] = []
    excludes: list[str] = []
    for code in codes:
        pos = _category_position(norm, code)
        kind = "add"
        for verb_pos, verb_kind in verbs:
            if verb_pos <= pos:
                kind = verb_kind
            else:
                break
        (excludes if kind == "remove" else adds).append(code)
    return tuple(adds), tuple(excludes)


def interpret_refinement(instruction: str | None) -> RefinementPlan:
    """Turn a delta instruction into one typed operation.

    Deterministic and dependency-free so it works in degraded mode.  An empty
    instruction is the "keep" no-op; anything we cannot map to add/remove/
    reorder is refused with a machine reason code instead of being ignored.
    """
    text = (instruction or "").strip()
    if not text:
        return RefinementPlan(operation="none")

    categories = _detected_categories(text)

    # 1. Reorder wins when the instruction asks for it explicitly ("отсортируй
    #    по времени посещения", "сначала самые длинные").  An ordering verb
    #    without a recognisable attribute is refused, not guessed.
    if _has(_REORDER_RE, text):
        by = _reorder_attribute(text)
        if by is None:
            code = REFINEMENT_REORDER_ATTRIBUTE_MISSING
            return RefinementPlan(
                operation="unsupported",
                reason_code=code,
                detail=REASON_TEXT[code],
            )
        return RefinementPlan(
            operation="reorder",
            reorder_by=by,
            descending=_has(_DESC_RE, text) and not _has(_ASC_RE, text),
        )

    # 2. A recognised-but-unsupported mutation verb ("сделай маршрут короче",
    #    "построй другой маршрут", "оптимизируй").  Checked before the bare
    #    superlative below so "маршрут короче" is not mistaken for "сначала
    #    самые короткие".
    if _has(_UNSUPPORTED_OP_RE, text):
        code = REFINEMENT_UNSUPPORTED
        return RefinementPlan(
            operation="unsupported",
            reason_code=code,
            detail=REASON_TEXT[code],
        )

    # 3+4. Add and/or remove.  A category whose nearest verb is a removal is an
    #    EXCLUSION ("без музеев", "исключи кафе") and must never be added; the
    #    same instruction may ask for both ("добавь кафе и убери музеи").
    add_verb = _has(_ADD_RE, text)
    remove_verb = _has(_REMOVE_RE, text)
    if add_verb or remove_verb or categories:
        add_cats, exclude_cats = _classify_categories(text, categories)
        remove_names = _removed_names(text) if remove_verb else ()
        op = "add" if (add_cats or (add_verb and not remove_verb)) else "remove"
        return RefinementPlan(
            operation=op,
            add_categories=add_cats,
            add_names=_add_names(text, add_cats) if (add_verb or add_cats) else (),
            exclude_categories=exclude_cats,
            remove_names=remove_names,
        )

    # 5. A bare order-attribute phrase with no ordering verb ("по времени
    #    посещения", "по расстоянию от старта") is still a reorder request.
    if _has(_VISIT_ATTR_RE, text) or _has(_DIST_ATTR_RE, text):
        by = "visit_minutes" if _has(_VISIT_ATTR_RE, text) else "distance"
        return RefinementPlan(
            operation="reorder",
            reorder_by=by,
            descending=_has(_DESC_RE, text) and not _has(_ASC_RE, text),
        )

    # 6. Nothing matched: be honest rather than silently ignoring the user.
    code = REFINEMENT_UNRECOGNIZED
    return RefinementPlan(
        operation="unsupported",
        reason_code=code,
        detail=REASON_TEXT[code],
    )


def _reorder_attribute(text: str) -> ReorderBy | None:
    """Which attribute a reorder instruction names, or None."""
    if _has(_VISIT_ATTR_RE, text):
        return "visit_minutes"
    if _has(_DIST_ATTR_RE, text):
        return "distance"
    return None


def _add_names(text: str, categories: tuple[str, ...]) -> tuple[str, ...]:
    """Capitalised/unknown nouns the instruction asks to add by name.

    A category word is never a name (it is already in `add_categories`); the
    pipeline still has to resolve what is left against the DB, and drops
    whatever does not resolve.
    """
    norm = _norm(text)
    match = _ADD_RE.search(norm)
    start = match.end() if match else 0
    cats = set(categories)
    out: list[str] = []
    for token in _TOKEN_RE.findall(norm[start:]):
        if len(token) < 3 or token in _STOPWORDS:
            continue
        if _has(_ADD_RE, token) or _has(_REMOVE_RE, token):
            continue
        if taxonomy.resolve_code(token):
            continue
        if token in cats:
            continue
        out.append(token)
    return tuple(dict.fromkeys(out))


# ── Reordering ──────────────────────────────────────────────────────────────


def visit_minutes_of(candidate: Candidate) -> int:
    """The stop's own visit estimate, falling back to its category default.

    This is an estimate from the taxonomy, not a hard constraint: a refinement
    like "more time here" must not be treated as a scheduling rule.
    """
    if candidate.visit_minutes_db is not None:
        return int(candidate.visit_minutes_db)
    return visit_time_minutes(candidate.category)


def stop_category_code(candidate: Candidate) -> str | None:
    """Canonical taxonomy code behind a candidate's category value, or None."""
    raw = (candidate.category or "").strip()
    if not raw:
        return None
    if raw in taxonomy.all_codes():
        return raw
    return taxonomy.resolve_code(raw)


def is_excluded_category(candidate: Candidate, codes: tuple[str, ...]) -> bool:
    """True when the stop's category is one the instruction asked to exclude.

    The comparison is on the canonical code, so a route row stored as
    "католический костёл" is matched by the code "костёл" just as "костёл" is.
    """
    if not codes:
        return False
    code = stop_category_code(candidate)
    return code is not None and code in codes


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (fallback when no road matrix exists)."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def reorder_stops(
    stops: list[Candidate],
    *,
    by: ReorderBy,
    descending: bool = False,
    origin: LatLon | None = None,
    matrix: list[list[float]] | None = None,
) -> list[Candidate]:
    """Order the EXISTING stops by one attribute, without dropping any.

    * ``by="visit_minutes"`` — each stop's own visit estimate
      (``visit_minutes_db`` else the category default).  Largest-last by
      default; ``descending`` puts the longest visits first.
    * ``by="distance"`` — walking distance from the route start.  The start is
      the tourist's ``origin`` when known, else the stop the route already
      starts with.  Uses the road matrix when one is supplied (matrix indices
      must match ``stops``), else straight-line distance.

    The sort is stable, so equal keys keep their previous relative order.
    """
    if len(stops) < 2:
        return list(stops)

    if by == "visit_minutes":
        keyed = [(visit_minutes_of(c), i, c) for i, c in enumerate(stops)]
    elif by == "distance":
        dist = _distance_keys(stops, origin=origin, matrix=matrix)
        keyed = [(dist[i], i, c) for i, c in enumerate(stops)]
    else:
        raise ValueError(f"unknown reorder attribute: {by!r}")

    # Stable in both directions: descending negates the attribute only, so
    # equal keys keep their previous relative order.
    if descending:
        keyed.sort(key=lambda t: (-t[0], t[1]))
    else:
        keyed.sort(key=lambda t: (t[0], t[1]))
    return [c for _k, _i, c in keyed]


def _distance_keys(
    stops: list[Candidate],
    *,
    origin: LatLon | None,
    matrix: list[list[float]] | None,
) -> list[float]:
    """Distance in metres from the route start, per stop (matrix-aware)."""
    if origin is not None:
        return [haversine_m(origin.lat, origin.lon, c.lat, c.lon) for c in stops]

    # No GPS start: the route's current first stop is the start.
    start = stops[0]
    if matrix is not None and len(matrix) == len(stops):
        return [float(matrix[0][i]) for i in range(len(stops))]
    return [haversine_m(start.lat, start.lon, c.lat, c.lon) for c in stops]


def reason_text(reason_code: str) -> str:
    """Human fallback wording for a machine reason code."""
    return REASON_TEXT.get(
        reason_code,
        "Это изменение маршрута пока не поддерживается — оставили маршрут как есть.",
    )

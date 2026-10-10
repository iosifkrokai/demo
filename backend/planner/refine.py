"""Route refinement: interpret a delta instruction, honestly."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

from contracts.planner import Candidate, LatLon, RouteChange, RouteChanges
from core import constants
from db.models.place import Place
from db.store.places import PostgresPlaceRepository
from reference import taxonomy

from .cost import visit_time_minutes
from .resolve import CATEGORY_SYNONYMS, CATEGORY_SYNONYMS_EN
from .retrieve import candidate_of

REFINEMENT_UNSUPPORTED = "refinement_unsupported"
REFINEMENT_UNRECOGNIZED = "refinement_unrecognized"
REFINEMENT_REORDER_ATTRIBUTE_MISSING = "refinement_reorder_attribute_missing"

RefinementOperation = Literal["none", "add", "remove", "reorder", "unsupported"]
ReorderBy = Literal["visit_minutes", "distance"]

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

_VISIT_ATTR_RE = re.compile(
    r"(?<![а-яa-z])(?:врем\w*|длительн\w*|минут\w*|"
    r"длинн\w*|дольше|долг\w*|коротк\w*|короч\w*|"
    r"time|duration|minutes)(?![а-яa-z])",
    re.IGNORECASE,
)
_DIST_ATTR_RE = re.compile(
    r"(?<![а-яa-z])(?:расстоян\w*|удал[её]нн\w*|дальност\w*|"
    r"от\s+старта|от\s+начала|ближайш\w*|ближе|"
    r"distance|nearest|from\s+the\s+start)(?![а-яa-z])",
    re.IGNORECASE,
)
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

_STOPWORDS = frozenset(
    ["и", "в", "во", "на", "с", "со", "по", "для", "от", "до", "из", "за", "у", "к", "о", "об", "это", "тот", "этот", "эти", "пожалуйста", "маршрут", "маршруте", "остановку", "остановки", "точки", "точку", "лишнее", "всё", "все", "все", "всё-таки", "только", "ещё", "еще"]
)


def _norm(text: str) -> str:
    return (text or "").strip().lower().replace("ё", "е")


def _has(pattern: re.Pattern[str], text: str) -> bool:
    return bool(pattern.search(text or ""))


@dataclass(frozen=True)
class RefinementPlan:
    """``supported`` is the honest switch: when False the pipeline keeps the previous
    route and reports ``reason_code``.
    """

    operation: RefinementOperation = "none"

    add_categories: tuple[str, ...] = ()
    add_names: tuple[str, ...] = ()

    exclude_categories: tuple[str, ...] = ()

    remove_names: tuple[str, ...] = ()

    reorder_by: ReorderBy | None = None
    descending: bool = False

    reason_code: str | None = None
    detail: str | None = None

    @property
    def supported(self) -> bool:
        return self.operation != "unsupported"

    @property
    def cats(self) -> tuple[str, ...]:
        """Every category code named by the instruction (add + exclude)."""
        return self.add_categories + self.exclude_categories


def _detected_categories(text: str) -> tuple[str, ...]:
    """Canonical category codes named in the instruction, in taxonomy order.

    Uses the same surface-form maps retrieval and intent extraction use.
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

    return tuple(c for c in taxonomy.all_codes() if c in hits)


def _removed_names(text: str) -> tuple[str, ...]:
    """Tokens the instruction asks to remove ("убери форт" → ("форт",))."""
    norm = _norm(text)
    if not _REMOVE_RE.search(norm):
        return ()
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

    A category whose nearest preceding verb is a removal is an exclusion, never an addition.
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
    """Turn a delta instruction into one typed operation."""
    text = (instruction or "").strip()
    if not text:
        return RefinementPlan(operation="none")

    categories = _detected_categories(text)

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

    if _has(_UNSUPPORTED_OP_RE, text):
        code = REFINEMENT_UNSUPPORTED
        return RefinementPlan(
            operation="unsupported",
            reason_code=code,
            detail=REASON_TEXT[code],
        )

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

    if _has(_VISIT_ATTR_RE, text) or _has(_DIST_ATTR_RE, text):
        by = "visit_minutes" if _has(_VISIT_ATTR_RE, text) else "distance"
        return RefinementPlan(
            operation="reorder",
            reorder_by=by,
            descending=_has(_DESC_RE, text) and not _has(_ASC_RE, text),
        )

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

    A category word is never a name; the pipeline resolves what is left against the DB.
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


def visit_minutes_of(candidate: Candidate) -> int:
    """The stop's own visit estimate, falling back to its category default.

    An estimate from the taxonomy, not a hard constraint.
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

    The comparison is on the canonical code, so "католический костёл" matches "костёл".
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


def _with_base_points(
    candidates: list[Candidate],
    rows: list[Place],
    excluded: set[int],
) -> tuple[list[Candidate], list[Candidate]]:
    """Stops from the previous turn are re-added after every trim; only an explicit
    request or Valhalla's verdict drops one.
    """
    base = [candidate_of(place, 0.0) for place in rows if place.id not in excluded]
    have = {c.id for c in candidates}
    merged = list(candidates) + [b for b in base if b.id not in have]
    return merged, base


def _nearby_convenience(
    places: PostgresPlaceRepository | None,
    base: list[Candidate],
    wanted: set[str],
    *,
    radius_m: int = constants.CONVENIENCE_RADIUS_M,
    max_added: int = constants.CONVENIENCE_MAX_ADDED,
) -> list[Candidate]:
    """Convenience stops (coffee, toilet, ...) that sit ON the route."""
    if places is None:
        return []
    found: dict[int, Candidate] = {}
    per_stop: dict[int, int] = {}
    for stop in base:
        if stop.id is None or per_stop.get(stop.id, 0) >= 2:
            continue
        near = places.nearby(stop.lat, stop.lon, radius_km=radius_m / 1000.0, limit=8)
        for place in near:
            if place.id is None:
                continue
            cat = (place.category or "").strip().lower()
            if cat not in wanted or place.id in found:
                continue
            if place.id in {c.id for c in base}:
                continue
            found[place.id] = candidate_of(place, 0.0)
            per_stop[stop.id] = per_stop.get(stop.id, 0) + 1
            if len(found) >= max_added:
                return list(found.values())
    return list(found.values())


def _cap_for_valhalla(
    candidates: list[Candidate],
    base: list[Candidate],
    limit: int = constants.VALHALLA_MAX_LOCATIONS,
) -> list[Candidate]:
    """Keep the ordering request inside Valhalla's location limit."""
    if len(candidates) <= limit:
        return candidates
    base_ids = {c.id for c in base}
    ordered = [c for c in candidates if c.id in base_ids]
    rest = [c for c in candidates if c.id not in base_ids]
    rest.sort(key=lambda c: c.relevance, reverse=True)
    return (ordered + rest)[:limit]


def _context_changes(base: list[Candidate], route: list[Candidate]) -> RouteChanges:
    """What a refinement did to the previous route — added / dropped / kept."""
    base_ids = {c.id for c in base}
    route_ids = {c.id for c in route}
    return RouteChanges(
        added=[
            RouteChange(id=c.id, name=c.name)
            for c in route
            if c.id not in base_ids
        ],
        removed=[
            RouteChange(
                id=c.id,
                name=c.name,
                reason="не связано дорогами или не уложилось в лимит",
            )
            for c in base
            if c.id not in route_ids
        ],
        kept=len(base_ids & route_ids),
    )


def _drop_excluded(candidates: list[Candidate], excluded_ids: set[int]) -> list[Candidate]:
    """Drop candidates the user removed by hand.

    Without this the same POI returns on every rebuild and the deletion looks ignored.
    """
    return [c for c in candidates if c.id not in excluded_ids]

"""Step 2 — Resolve constraints.

Merges IntentDecision with explicit client parameters (budget, bbox, names).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from core import constants
from db.models.place import Place
from db.store.places import PostgresPlaceRepository
from planner.models import IntentResult, ResolvedConstraints

log = logging.getLogger(__name__)

CATEGORY_SYNONYMS: dict[str, list[str]] = {
    "замок": [
        "замок", "замка", "замку", "замком", "замке",
        "замки", "замков", "замкам", "замками", "замках",
        "крепость", "крепости", "крепостью", "крепостей",
        "крепостям", "крепостями", "крепостях",
    ],
    "костёл": [
        "костёл", "костёла", "костёлу", "костёлом", "костёле",
        "костёлы", "костёлов", "костёлам", "костёлами", "костёлах",
        "костел", "костела", "костелу", "костелем", "костеле",
        "костелы", "костелов", "костелам", "костелами", "костелах",
    ],
    "церковь": [
        "церковь", "церкви", "церковью", "церквей",
        "церковям", "церковями", "церквях",
    ],
    "монастырь": [
        "монастырь", "монастыря", "монастырю", "монастырем", "монастыре",
        "монастыри", "монастырей", "монастырям", "монастырями", "монастырях",
    ],
    "дворец": [
        "дворец", "дворца", "дворцу", "дворцом", "дворце",
        "дворцы", "дворцов", "дворцам", "дворцами", "дворцах",
    ],
    "усадьба": [
        "усадьба", "усадьбы", "усадьбу", "усадьбе", "усадьбой",
        "усадьбам", "усадьбами", "усадьбах", "усадеб",
        "резиденция", "резиденции", "резиденцию", "резиденцией",
    ],
    "парк": [
        "парк", "парка", "парку", "парком", "парке",
        "парки", "парков", "паркам", "парками", "парках",
        "сквер", "сквера", "скверу", "сквером", "сквере",
    ],
    "музей": [
        "музей", "музея", "музею", "музеем", "музее",
        "музеи", "музеев", "музеям", "музеями", "музеях",
        "галерея", "галереи", "галерею", "галерее", "галереей",
    ],
    "памятник": [
        "памятник", "памятника", "памятнику", "памятником", "памятнике",
        "памятники", "памятников", "памятникам", "памятниками", "памятниках",
        "монумент", "монумента", "монументу", "монументом", "монументе",
    ],
    "храм": [
        "храм", "храма", "храму", "храмом", "храме",
        "храмы", "храмов", "храмам", "храмами", "храмах",
        "кирха", "кирхи", "кирху", "кирхе", "кирхой",
        "синагога", "синагоги", "синагогу", "синагоге", "синагогой",
        "каплица", "каплицы", "каплицу", "каплице", "каплицей",
    ],
    "архитектура": [
        "архитектура", "архитектуры", "архитектуру", "архитектурой", "архитектурный",
        "здание", "здания", "зданий", "зданию", "зданием",
        "театр", "театра", "театру", "театром", "театре", "театров",
    ],
    "инфраструктура": [
        "инфраструктура", "инфраструктуры", "инфраструктуру",
        "мост", "моста", "мосту", "мостом", "мосте", "мосты", "мостов",
        "башня", "башни", "башню", "башне", "башней",
        "набережная", "набережной", "набережную", "набережною",
        "шлюз", "шлюза", "шлюзу", "шлюзом", "шлюзе", "шлюзы", "шлюзов",
    ],
    "кладбище": [
        "кладбище", "кладбища", "кладбищу", "кладбищем",
        "кладбищам", "кладбищами", "кладбищах",
        "некрополь", "некрополя", "некрополю", "некрополем", "некрополе",
    ],
    "кафе": [
        "кафе",
        "кофейня", "кофейни", "кофейне", "кофейню", "кофейной",
        "кофе",
    ],
    "ресторан": [
        "ресторан", "ресторана", "ресторану", "рестораном", "ресторане", "ресторанов",
        "столовая", "столовой", "столовую", "столовою",
        "поесть", "пообедать", "покушать",
        "обед", "обеда", "обеду", "обедом", "обеде",
    ],
    "туалет": [
        "туалет", "туалета", "туалету", "туалетом", "туалете", "туалеты", "туалетов",
        "уборная", "уборной", "уборную", "уборною",
        "санузел", "санузла", "санузлу", "санузлом", "санузле",
        "санузлы", "санузлов", "санузлам", "санузами", "санузах",
        "wc",
        "чтобы туалеты по пути были",
    ],
    "гостиница": [
        "гостиница", "гостиницы", "гостиницу", "гостинице", "гостиницей",
        "отель", "отеля", "отелю", "отелем", "отеле", "отелей",
        "хостел", "хостела", "хостелу", "хостелом", "хостеле",
        "гостевой дом",
    ],
    "остановка транспорта": [
        "автобус", "автобуса", "автобусу", "автобусом", "автобусе",
        "автобусы", "автобусов", "автобусам", "автобусах",
        "троллейбус", "троллейбуса", "троллейбусу", "троллейбусом", "троллейбусе",
        "троллейбусы", "троллейбусов",
        "маршрутка", "маршрутки", "маршрутку", "маршрутке", "маршруткой", "маршруток",
    ],
}

CATEGORY_SYNONYMS_EN: dict[str, list[str]] = {
    "замок": [
        "castle", "castles", "fortress", "fortresses", "citadel", "citadels",
        "stronghold", "strongholds", "keep",
    ],
    "костёл": ["cathedral", "cathedrals"],
    "церковь": ["church", "churches"],
    "монастырь": [
        "monastery", "monasteries", "convent", "convents", "abbey", "abbeys",
        "friary",
    ],
    "дворец": ["palace", "palaces"],
    "усадьба": [
        "estate", "estates", "manor", "manors", "mansion", "mansions",
        "residence", "residences", "homestead",
    ],
    "парк": ["park", "parks", "garden", "gardens"],
    "музей": ["museum", "museums", "gallery", "galleries"],
    "памятник": [
        "monument", "monuments", "memorial", "memorials", "statue", "statues",
    ],
    "храм": [
        "temple", "temples", "synagogue", "synagogues", "chapel", "chapels",
        "shrine", "shrines",
    ],
    "архитектура": [
        "architecture", "building", "buildings", "theater", "theatre",
    ],
    "инфраструктура": [
        "bridge", "bridges", "embankment", "tower", "towers", "lock", "locks",
        "waterfront",
    ],
    "кладбище": ["cemetery", "cemeteries", "graveyard", "necropolis"],
    "кафе": [
        "cafe", "cafes", "café", "cafés", "coffee", "cafeteria", "cafeterias",
        "coffeehouse",
    ],
    "ресторан": [
        "restaurant", "restaurants", "canteen", "canteens", "lunch", "dinner",
        "dining", "eat",
    ],
    "туалет": [
        "toilet", "toilets", "restroom", "restrooms", "wc", "bathroom",
        "bathrooms", "lavatory",
    ],
    "гостиница": [
        "hotel", "hotels", "hostel", "hostels", "inn", "inns", "lodging",
    ],
    "остановка транспорта": [
        "bus stop", "bus stops", "trolleybus stop", "tram stop",
        "transit stop", "boarding point",
    ],
}


def resolve(
    intent: IntentResult,
    *,
    explicit_time_budget: int | None = None,
    explicit_bbox: list[float] | None = None,
    explicit_round_trip: bool = False,
    outside: Sequence[str] = (),
    places: PostgresPlaceRepository,
) -> ResolvedConstraints:
    d = intent.decision

    if explicit_time_budget is not None:
        budget = explicit_time_budget or None
    else:
        budget = d.time_budget_minutes or None
    if budget is not None:
        budget = max(constants.MIN_BUDGET_MIN, min(budget, constants.MAX_BUDGET_MIN))

    bbox: tuple[float, float, float, float] | None = None
    if explicit_bbox is not None and len(explicit_bbox) == 4:
        s, w, n, e = (float(v) for v in explicit_bbox)
        bbox = (w, s, e, n)

    must_visit_ids, area_anchor, resolved_names = _resolve_named_places(
        d.named_places, places, outside
    )

    must_visit_ids = _without_forbidden(must_visit_ids, d.categories_neg, places)

    must_visit_keywords = _expand_categories_to_keywords(d.categories_pos)
    must_visit_keywords.extend(d.keywords_pos)

    era_hint = d.era_hint if d.era_hint in ("any", "pre1900", "soviet", "modern") else "any"

    return ResolvedConstraints(
        must_visit_ids=must_visit_ids,
        resolved_names=resolved_names,
        area_anchor=area_anchor,
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
        round_trip=explicit_round_trip,
    )


def _is_location_suffix(name: str, query: str) -> bool:
    """Return True if name ends with '(Location)' or is just the location name.

    These are location rows (not specific POIs) and become area_anchors.
    """
    q = query.strip()
    if name.lower() == q:
        return True
    if len(q) >= 3:
        suffix = f" ({q.lower()})"
        if name.lower().endswith(suffix):
            return True
    return False


def _without_forbidden(
    ids: list[int], forbidden: Sequence[str], places: PostgresPlaceRepository
) -> list[int]:
    """Drop must-visit ids whose own category the request forbids."""
    if not ids or not forbidden:
        return ids

    category_of = places.category_of(ids)

    out: list[int] = []
    for pid in ids:
        if category_of.get(pid) in forbidden:
            log.warning(
                "resolve: must_visit id=%s (%s) contradicts the request's own "
                "«без %s» — kept out", pid, category_of.get(pid), category_of.get(pid),
            )
            continue
        out.append(pid)
    return out


def _resolve_named_places(
    names: list[str], places: PostgresPlaceRepository, outside: Sequence[str] = ()
) -> tuple[list[int], int | None, list[str]]:
    """Match named place strings to place IDs, distinguishing POI names from areas.

    Returns (must_visit_ids, area_anchor, resolved_names).
    """
    must_out: list[int] = []
    seen: set[int] = set()
    area_anchor: int | None = None
    resolved: list[str] = []
    reported_outside = {n.strip().lower() for n in outside if n and n.strip()}

    for name in names:
        if not name or not name.strip():
            continue

        strict = name.strip().lower() in reported_outside

        name_matches = places.name_match(name, limit=3)
        if name_matches:
            top, sim = name_matches[0]
            top_name = top.name
            if _is_location_suffix(top_name, name):
                pass
            elif (
                (sim >= constants.NAME_MATCH_MIN_SIM if not strict else _same_name(top_name, name))
                and top.id is not None
                and top.id not in seen
            ):
                seen.add(top.id)
                must_out.append(top.id)
                resolved.append(name)
                continue

        if area_anchor is None:
            for place in places.keyword_search(name, limit=5):
                if place.name.strip().lower() == name.strip().lower():
                    continue
                if _is_town_or_district_match(place, name):
                    if place.id is not None and place.id not in seen:
                        area_anchor = place.id
                    break

    return must_out, area_anchor, resolved


def _same_name(row_name: str, wanted: str) -> bool:
    """Is `row_name` the very name asked for — not merely a similar one?

    Allows the town suffix the data carries, and nothing more.
    """
    a = " ".join((row_name or "").lower().split())
    b = " ".join((wanted or "").lower().split())
    if not a or not b:
        return False
    if a == b:
        return True
    head = a.split("(", 1)[0].strip()
    return head == b or a.startswith(b + " ") or b.startswith(a + " ")


def _word_boundary_match(text: str, query: str) -> bool:
    """Return True if query appears as a standalone word in text.

    Word boundaries prevent "мир" matching inside "мискому".
    """
    import re as _re
    if not text or not query:
        return False
    return _re.search(r"\b" + _re.escape(query) + r"\b", text) is not None


def _is_town_or_district_match(place: Place, query: str) -> bool:
    """Return True if the query matched on town or district.

    Uses word-boundary regex for all three fields to prevent false matches.
    """
    name = (place.name or "").lower()
    town = (place.town or "").lower()
    district = (place.district or "").lower()
    q = query.lower()

    if _word_boundary_match(town, q):
        return True
    if _word_boundary_match(district, q):
        return True

    if _word_boundary_match(name, q):
        return False

    return False


def _expand_categories_to_keywords(categories: Sequence[str]) -> list[str]:
    """Convert LLM categories into additional Russian synonyms for retrieval."""
    out: list[str] = []
    for c in categories:
        syns = CATEGORY_SYNONYMS.get(c, [c])
        for s in syns:
            if s and s not in out:
                out.append(s)
    return out

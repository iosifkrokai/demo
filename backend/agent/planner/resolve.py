"""Step 2 — Resolve constraints.

Merges IntentDecision with explicit client parameters:
  * Time budget: explicit client > LLM > default (clamped to [MIN, MAX]).
  * Bbox:        explicit client wins; otherwise None (whole Grodno).
  * Named places → area_anchor (geo focus) or must_visit_ids (real POI name match).

Rule for named-place resolution:
  - A NAME match (similarity threshold MET) → the POI is a real place the user
    explicitly asked for → must_visit_ids.  Example: "Мирскому замку" → Mir Castle.
  - A TOWN / DISTRICT match only → the token names an area, not a specific POI →
    area_anchor.  The pipeline uses it to set the geo focus; it does NOT force
    a POI into the route.  Example: "Гродно" in "замки Гродно" → geo anchor only.

Outputs ResolvedConstraints consumed by retrieve/optimize/etc.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import psycopg

from contracts.planner import IntentResult, ResolvedConstraints
from domain import constants
from store.search import _keyword_search, _name_match_search

log = logging.getLogger(__name__)

# Russian synonym expansion for retrieval — category → search keywords.
#
# Each list is the full declension of the head noun plus its synonyms,
# because Russian inflection breaks naive matching: the query «по замкам»,
# the POI name «Новый замок» and the keyword «замки» are one category, and
# every surface form has to be known.  This single map is the taxonomy:
# planner/retrieve.py reads category words off the query, and the no-LLM
# intent fallback (planner/intent.py) inverts it to fill categories_pos —
# the taxonomy lives HERE, never duplicated per call site.
CATEGORY_SYNONYMS: dict[str, list[str]] = {
    # heritage taxonomy
    "замок": [
        "замок", "замка", "замку", "замком", "замке",
        "замки", "замков", "замкам", "замками", "замках",
        "крепость", "крепости", "крепостью", "крепостей",
        "крепостям", "крепостями", "крепостях",
    ],
    "костёл": [
        "костёл", "костёла", "костёлу", "костёлом", "костёле",
        "костёлы", "костёлов", "костёлам", "костёлами", "костёлах",
        # without ё — as written in queries and OSM names
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
    # everyday stops (OSM amenity/tourism POIs)
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
    # Boarding points. A tourist walking a route asks «где сесть на автобус», and
    # the deterministic fallback has to read that without a model — so the three
    # everyday words for a stop are registered here, not only in taxonomy.csv.
    # The bare word «остановка» is deliberately NOT among them: to a tourist it
    # means a stop on the walk («с обязательной остановкой у Фарного костёла»),
    # and reading that as a bus stop sent a church request hunting for transport.
    # Only the transport-qualified forms count.
    "остановка транспорта": [
        "автобус", "автобуса", "автобусу", "автобусом", "автобусе",
        "автобусы", "автобусов", "автобусам", "автобусах",
        "троллейбус", "троллейбуса", "троллейбусу", "троллейбусом", "троллейбусе",
        "троллейбусы", "троллейбусов",
        "маршрутка", "маршрутки", "маршрутку", "маршрутке", "маршруткой", "маршруток",
    ],
}

# English surface forms, same shape and same keys as CATEGORY_SYNONYMS.
#
# Spec 002 makes the product RU/EN, and the deterministic (no-LLM) fallback has
# to read an English query too.  This map lives HERE, next to CATEGORY_SYNONYMS,
# so the taxonomy is still one file per concern: the eventual W1 `taxonomy.py`
# becomes the single source and this map is folded into it.  Forms are
# lower-case; a form that names an ambiguous concept is mapped to the generic
# code ("church" → церковь, not the Catholic костёл).
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
    # English: "stop" alone is as ambiguous as the Russian «остановка» (a stop on
    # the walk), so only the transport-qualified forms are registered.
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
    db: psycopg.Connection,
) -> ResolvedConstraints:
    d = intent.decision

    # Time budget: explicit > LLM > none
    # No time limit stated by the user → no limit at all. A default of 120 min
    # used to be applied silently, which trimmed the route to whatever fit two
    # hours the user never asked for. 0 is the UI's "без ограничения" value and
    # means exactly the same as an absent field.
    if explicit_time_budget is not None:
        # The selector wins over any model guess; 0 = "без ограничения".
        budget = explicit_time_budget or None
    else:
        budget = d.time_budget_minutes or None
    if budget is not None:
        budget = max(constants.MIN_BUDGET_MIN, min(budget, constants.MAX_BUDGET_MIN))

    # Bbox: explicit wins; else None. Format: (W, S, E, N) — matches ST_MakeEnvelope.
    bbox: tuple[float, float, float, float] | None = None
    if explicit_bbox is not None and len(explicit_bbox) == 4:
        # Reorder from [south, west, north, east] (HTTP) to (W, S, E, N).
        s, w, n, e = (float(v) for v in explicit_bbox)
        bbox = (w, s, e, n)

    # Named places → must_visit_ids + area_anchor
    #   must_visit_ids  : real POI name matches (definite places the user named)
    #   area_anchor     : first town/district-only match (for geo focus), or None
    must_visit_ids, area_anchor, resolved_names = _resolve_named_places(
        d.named_places, db, outside
    )

    # The request's own prohibition outranks a place inferred from its words.
    # «вечерняя прогулка по Советской, без музеев» gives the fragment «Советской»,
    # which name-matches «Аптека-музей на Советской» → must-visit, and a must-visit
    # *bypasses* the negative filter (see retrieve.apply_negative_filter) — so the
    # user's «без музеев» was violated by a place our own reader had invented. Such
    # an id is kept out here; the must-visit name stays in the contract and the
    # verifier reports it honestly as absent.
    must_visit_ids = _without_forbidden(must_visit_ids, d.categories_neg, db)

    # Build must_visit_keywords (used by retrieval as a strong positive signal)
    must_visit_keywords = _expand_categories_to_keywords(d.categories_pos)
    must_visit_keywords.extend(d.keywords_pos)

    # Era hint: pass through
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
        # A visible UI choice: the tourist asked for a closed tour.
        round_trip=explicit_round_trip,
    )


def _is_location_suffix(name: str, query: str) -> bool:
    """Return True if name ends with '(Location)' or is just the location name.

    E.g. name='Лютеранская кирха (Гродно)' and query='Гродно' → True.
         name='Гродно' and query='Гродно' → True.
    These are location rows (not specific POIs) and should become area_anchors.
    """
    q = query.strip()
    if name.lower() == q:
        return True
    # Check for parenthetical location: "POI (Location)" pattern at the end
    # " (Гродно)" has 8 chars
    if len(q) >= 3:
        suffix = f" ({q.lower()})"
        if name.lower().endswith(suffix):
            return True
    return False


def _without_forbidden(
    ids: list[int], forbidden: Sequence[str], db: psycopg.Connection
) -> list[int]:
    """Drop must-visit ids whose own category the request forbids."""
    if not ids or not forbidden:
        return ids

    rows = db.execute(
        "SELECT id, category FROM places WHERE id = ANY(%s)", (list(ids),)
    ).fetchall()
    category_of = {r[0]: r[1] for r in rows}

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
    names: list[str], db: psycopg.Connection, outside: Sequence[str] = ()
) -> tuple[list[int], int | None, list[str]]:
    """Match named place strings to place IDs, distinguishing POI names from areas.

    Returns (must_visit_ids, area_anchor):
      - must_visit_ids : POI-name matches only (NAME column similarity >= threshold)
      - area_anchor     : first town/district-only match (for geo focus), or None

    The heuristic:
      1. Try NAME-only search (strict) → must_visit if similarity >= NAME_MATCH_MIN_SIM.
      2. If no name match, try town/district search → area_anchor (not must_visit).
    """
    must_out: list[int] = []
    seen: set[int] = set()
    area_anchor: int | None = None
    resolved: list[str] = []
    reported_outside = {n.strip().lower() for n in outside if n and n.strip()}

    for name in names:
        if not name or not name.strip():
            continue

        # A name the reader placed outside the region is matched STRICTLY: it may
        # only resolve to a place whose name really is that name (allowing for a
        # town suffix). Similarity alone used to hand a Vilnius cathedral the
        # Lida one (id 554) — a plausible-looking substitution for a place that
        # does not exist in this region, which then anchored a route in the wrong
        # town and answered `ready`. A name that does not resolve this way stays
        # unresolved, and the planner refuses the request instead.
        strict = name.strip().lower() in reported_outside

        # Step 1: name-only search — strict, high-quality matches only.
        name_rows = _name_match_search(db, name, limit=3)
        if name_rows:
            top = name_rows[0]
            sim = top.get("_name_sim", 0.0)
            top_name = top.get("name", "")
            # Skip if the name match is just a location suffix in parentheses
            # (e.g. "Лютеранская кирха (Гродно)" matched by "Гродно").
            # These are area names, not specific POIs — fall through to area check.
            if _is_location_suffix(top_name, name):
                pass  # don't add to must_visit; fall through to area check below
            elif (
                (sim >= constants.NAME_MATCH_MIN_SIM if not strict else _same_name(top_name, name))
                and top["id"] not in seen
            ):
                seen.add(top["id"])
                must_out.append(top["id"])
                resolved.append(name)
                continue  # resolved as a real POI; don't also use as area anchor

        # Step 2: town/district search — area anchor only, NOT a must-visit.
        # Only take the first town-match as the area anchor (preserve order).
        if area_anchor is None:
            town_rows = _keyword_search(db, name, limit=5)
            for row in town_rows:
                row_name = row.get("name", "")
                # Skip a row that IS the location itself (a town/area row such as
                # name="Гродно").  POIs whose name merely carries the town in
                # parentheses ("Старый замок (Гродно)") are valid anchors — the
                # anchor only sets the geo focus, it does not force the POI into
                # the route, so a POI row is a fine anchor.
                if row_name.strip().lower() == name.strip().lower():
                    continue
                if _is_town_or_district_match(row, name):
                    if row["id"] not in seen:
                        area_anchor = row["id"]
                    break

    return must_out, area_anchor, resolved


def _same_name(row_name: str, wanted: str) -> bool:
    """Is `row_name` the very name asked for — not merely a similar one?

    Allows the town suffix the data carries («Старый замок» for «Старый замок
    (Гродно)») and nothing more: a different cathedral in a different town is a
    different place, however close its name looks. Used when the reader has
    already said the asked-for name is not in this region.
    """
    a = " ".join((row_name or "").lower().split())
    b = " ".join((wanted or "").lower().split())
    if not a or not b:
        return False
    if a == b:
        return True
    # The data's own spelling may add a parenthesised location: «… (Гродно)».
    head = a.split("(", 1)[0].strip()
    return head == b or a.startswith(b + " ") or b.startswith(a + " ")


def _word_boundary_match(text: str, query: str) -> bool:
    """Return True if query appears as a standalone word in text.

    Uses word-boundary regex to prevent "мир" matching inside "мискому".
    Both text and query are lowercased by the caller.
    """
    import re as _re
    if not text or not query:
        return False
    return _re.search(r"\b" + _re.escape(query) + r"\b", text) is not None


def _is_town_or_district_match(row: dict, query: str) -> bool:
    """Return True if the query matched on town or district.

    Uses word-boundary regex for ALL three fields to prevent false matches:
      - "Гродно" must NOT match district "Гродненский район" (substring).
      - "Лидский" must NOT match name "Лидский замок" (word inside name).
      - "Лидский" must match district "Лидский район" (standalone word).
    """
    name = (row.get("name") or "").lower()
    town = (row.get("town") or "").lower()
    district = (row.get("district") or "").lower()
    q = query.lower()

    # Town and district: word-boundary match (prevents "Гродно" matching "Гродненский").
    if _word_boundary_match(town, q):
        return True
    if _word_boundary_match(district, q):
        return True

    # Name word-boundary check: prevents "Лидский" in "Лидский замок" from
    # triggering a false town match.
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

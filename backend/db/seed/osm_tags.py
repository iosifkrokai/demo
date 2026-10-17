"""OSM tag → project category maps, and Overpass element → places-row conversion.
Turns raw OSM tags into a canonical category, a name and a 14-column places row.
"""

from __future__ import annotations

import re
from typing import Any

from . import taxonomy
from .geofence import inside_project_area

DEFAULT_VISIT_MINUTES = 20

_TICKET_TAGS = ("charge", "fee")
_PUNCTUATION_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)

_FORT_NAME = re.compile(
    r"\bдот\b|\bдзот\b|блиндаж|бункер|\bфорт\b|окоп|оборонит|убежищ|опорн\w*\s+пункт",
    re.IGNORECASE,
)


def visit_minutes_for(category: str) -> int:
    """Default visit time (minutes) for a taxonomy category, else the fallback."""
    try:
        return taxonomy.visit_minutes(category)
    except KeyError:
        return DEFAULT_VISIT_MINUTES


SOURCE_PREFIX = "osm_poi:"

AMENITY_CATEGORY = {
    "cafe": "кафе",
    "fast_food": "кафе",
    "restaurant": "ресторан",
    "toilets": "туалет",
}
TOURISM_CATEGORY = {
    "hotel": "гостиница",
    "hostel": "гостиница",
    "guest_house": "гостиница",
}
HIGHWAY_CATEGORY = {
    "bus_stop": "остановка транспорта",
}
PUBLIC_TRANSPORT_CATEGORY = {
    "platform": "остановка транспорта",
}
RAILWAY_CATEGORY = {
    "tram_stop": "остановка транспорта",
}

FALLBACK_LABEL_BY_CATEGORY = {
    "туалет": "Туалет",
    "остановка транспорта": "Остановка",
    "лестница": "Лестница",
}
SERVICE_FALLBACK_LABELS = frozenset(FALLBACK_LABEL_BY_CATEGORY.values())


def is_fallback_name(name: str | None) -> bool:
    """True for a generated service label, with or without its street hint."""
    head = (name or "").strip().split("(", 1)[0].strip()
    return head in SERVICE_FALLBACK_LABELS


Row = dict[str, str | float | None]


def sight_tag_to_category(tags: dict[str, str]) -> str | None:
    """Map OSM tags → our sight category, or ``None``. The order is the mapping: worship
    before the catch-alls, fortifications before named ``historic`` kinds, boulders after
    them; a bare ``historic=ruins`` is a condition that falls through."""
    return (
        _worship_category(tags)
        or _building_category(tags)
        or _fortification_category(tags)
        or _historic_category(tags.get("historic", ""))
        or _tourism_category(tags.get("tourism", ""))
        or _boulder_category(tags)
        or _generic_category(tags)
    )


def _fortification_category(tags: dict[str, str]) -> str | None:
    """A fort, pillbox, blindage or trench: the tags OSM states, else a matching name,
    since most such objects carry only a name such as «ДОТ Гродненского УР».
    """
    if tags.get("military") == "bunker" or tags.get("historic") == "fort":
        return "укрепление"
    if _FORT_NAME.search(_source_name(tags)):
        return "укрепление"
    return None


def _boulder_category(tags: dict[str, str]) -> str | None:
    """A named boulder or cult stone: OSM's `natural=stone` and `natural=rock`."""
    if tags.get("natural") in ("stone", "rock"):
        return "валун"
    return None


def _source_name(tags: dict[str, str]) -> str:
    """The name the tags carry, in the order `sight_name` prefers it."""
    for key in ("name:ru", "name", "name:be"):
        value = (tags.get(key) or "").strip()
        if value:
            return value
    return ""


def _historic_category(historic: str) -> str | None:
    """What the ``historic`` key alone settles — named kinds only: a castle in ruins stays
    ``замок``, ``historic=ruins`` alone decides nothing, and a manor has its own category.
    """
    if historic == "castle":
        return "замок"
    if historic == "manor":
        return "усадьба"
    if historic == "palace":
        return "дворец"
    if historic in ("monastery", "monastic_architecture"):
        return "монастырь"
    if historic in ("memorial", "monument"):
        return "памятник"
    return None


def _tourism_category(tourism: str) -> str | None:
    """What the `tourism` key alone settles — named kinds only, not the catch-all."""
    if tourism == "museum":
        return "музей"
    return None


def _generic_category(tags: dict[str, str]) -> str | None:
    """The catch-all tags, read last: they say "worth seeing" or "damaged", not what the
    thing is, so every sharper mapping speaks first.
    """
    if (
        tags.get("tourism") == "attraction"
        or tags.get("historic") == "archaeological_site"
        or tags.get("historic") == "ruins"
    ):
        return "архитектура"
    return None


def _worship_category(tags: dict[str, str]) -> str | None:
    """A place of worship reads by its religion, and a Christian one by its rite."""
    if tags.get("amenity", "") != "place_of_worship":
        return None
    religion = tags.get("religion", "")
    if religion == "christian":
        return _christian_rite(tags.get("denomination", ""))
    return "храм"


def _christian_rite(denomination: str) -> str:
    """The Orthodox rites read as церковь; every other one, named or not, as костёл."""
    if denomination in ("orthodox", "russian_orthodox", "old_believer"):
        return "церковь"
    return "костёл"


def _building_category(tags: dict[str, str]) -> str | None:
    """A worship building is a костёл for Catholics and a церковь for anyone else."""
    building = tags.get("building", "")
    if building in ("church", "cathedral", "chapel"):
        if tags.get("religion", "") != "christian":
            return "храм"
        return (
            "костёл"
            if tags.get("denomination", "") in ("catholic", "roman_catholic")
            else "церковь"
        )
    if building == "castle":
        return "замок"
    if building == "palace":
        return "дворец"
    return None


def service_tag_to_category(tags: dict[str, str]) -> str | None:
    """Map OSM amenity/tourism/transit tags → project category (or None)."""
    amenity = tags.get("amenity", "")
    if amenity in AMENITY_CATEGORY:
        return AMENITY_CATEGORY[amenity]
    tourism = tags.get("tourism", "")
    if tourism in TOURISM_CATEGORY:
        return TOURISM_CATEGORY[tourism]
    highway = tags.get("highway", "")
    if highway in HIGHWAY_CATEGORY:
        return HIGHWAY_CATEGORY[highway]
    if tags.get("public_transport", "") in PUBLIC_TRANSPORT_CATEGORY:
        return PUBLIC_TRANSPORT_CATEGORY[tags["public_transport"]]
    railway = tags.get("railway", "")
    if railway in RAILWAY_CATEGORY:
        return RAILWAY_CATEGORY[railway]
    return None


def sight_name(tags: dict[str, str]) -> str | None:
    """Prefer name:ru > name, skip if no Russian/Cyrillic name."""
    for key in ("name:ru", "name", "name:be"):
        val = tags.get(key, "")
        if val:
            return val
    return None


def ticket_price_of(tags: dict[str, str]) -> str:
    """The ``fee``/``charge`` tag as a price, or "" when it is only punctuation — a lone
    dash says nothing and is not a price.
    """
    for key in _TICKET_TAGS:
        value = (tags.get(key) or "").strip()
        if value and not _PUNCTUATION_ONLY.match(value):
            return value
    return ""


def service_name(tags: dict[str, str], category: str) -> str | None:
    """A name, else the category's fallback label, with the street: the street is what
    tells two unnamed stops apart offline.
    """
    name = tags.get("name") or tags.get("name:ru")
    if name:
        return name.strip()
    label = FALLBACK_LABEL_BY_CATEGORY.get(category)
    if label:
        street = (tags.get("addr:street") or "").strip()
        return f"{label} ({street})" if street else label
    return None


def sight_element_to_row(element: dict[str, Any]) -> Row | None:
    """Convert a single Overpass element → 14-column places row (or None)."""
    tags = element.get("tags", {})

    if element.get("type") == "node":
        lat = element.get("lat")
        lon = element.get("lon")
    else:
        center = element.get("center", {})
        lat = center.get("lat")
        lon = center.get("lon")

    if lat is None or lon is None:
        return None

    name = sight_name(tags)
    if not name:
        return None

    if not any("Ѐ" <= c <= "ӿ" for c in name):
        return None

    category = sight_tag_to_category(tags)
    if not category:
        return None

    if not inside_project_area(float(lat), float(lon)):
        return None

    osm_type = element.get("type", "unknown")
    osm_id = element.get("id")
    source_url = f"osm:{osm_type}/{osm_id}"

    return {
        "name": name,
        "category": category,
        "district": "",
        "town": tags.get("addr:city") or tags.get("addr:village") or "",
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
        "blurb": "",
        "fun_fact": "",
        "fun_facts": "[]",
        "opening_hours": tags.get("opening_hours", ""),
        "ticket_price": ticket_price_of(tags),
        "visit_minutes": str(visit_minutes_for(category)),
        "links": "[]",
        "source_url": source_url,
    }


def service_element_to_row(element: dict[str, Any]) -> Row | None:
    """Convert one Overpass element → a 14-column places row (or None).
    ``visit_minutes`` stays NULL; the planner derives it per category.
    """
    tags = element.get("tags", {})
    if element.get("type") == "node":
        lat, lon = element.get("lat"), element.get("lon")
    else:
        center = element.get("center", {})
        lat, lon = center.get("lat"), center.get("lon")
    if lat is None or lon is None:
        return None

    category = service_tag_to_category(tags)
    if not category:
        return None

    name = service_name(tags, category)
    if not name:
        return None

    if not inside_project_area(float(lat), float(lon)):
        return None

    osm_type = element.get("type", "unknown")
    osm_id = element.get("id")
    return {
        "name": name,
        "category": category,
        "district": "",
        "town": tags.get("addr:city") or tags.get("addr:village") or "",
        "lat": float(lat),
        "lon": float(lon),
        "blurb": "",
        "fun_fact": "",
        "fun_facts": "[]",
        "opening_hours": tags.get("opening_hours", ""),
        "ticket_price": ticket_price_of(tags),
        "visit_minutes": None,
        "links": "[]",
        "source_url": f"{SOURCE_PREFIX}{osm_type}/{osm_id}",
    }

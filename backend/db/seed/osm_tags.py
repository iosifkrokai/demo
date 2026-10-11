"""OSM tag → project category maps, and Overpass element → places-row conversion.
Turns raw OSM tags into a canonical category, a name and a 14-column places row.
"""

from __future__ import annotations

import math
from typing import Any

from reference import taxonomy
from reference.geofence import inside_project_area

DEFAULT_VISIT_MINUTES = 20


def visit_minutes_for(category: str) -> int:
    """Default visit time (minutes) for a taxonomy category, else the fallback."""
    try:
        return taxonomy.visit_minutes(category)
    except KeyError:
        return DEFAULT_VISIT_MINUTES


SOURCE_PREFIX = "osm_poi:"
DEDUP_RADIUS_M = 150.0

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

Row = dict[str, str | float | None]


def sight_tag_to_category(tags: dict[str, str]) -> str | None:
    """Map OSM tags → our sight category, or None."""
    historic = tags.get("historic", "")
    tourism = tags.get("tourism", "")
    amenity = tags.get("amenity", "")
    building = tags.get("building", "")
    religion = tags.get("religion", "")
    denomination = tags.get("denomination", "")

    if historic in ("castle", "manor", "ruins"):
        return "замок"
    if historic in ("monastery", "monastic_architecture"):
        return "монастырь"
    if historic in ("memorial", "monument"):
        return "памятник"
    if historic in ("archaeological_site",):
        return "архитектура"

    if tourism == "museum":
        return "музей"
    if tourism == "attraction":
        return "архитектура"

    if amenity == "place_of_worship":
        if religion == "christian":
            if denomination in ("catholic", "united", "roman_catholic"):
                return "костёл"
            if denomination in ("orthodox", "russian_orthodox", "old_believer"):
                return "церковь"
            return "костёл"
        if religion == "muslim":
            return "храм"
        if religion == "jewish":
            return "архитектура"
        return "храм"

    if building in ("church", "cathedral", "chapel"):
        if religion == "christian":
            if denomination in ("catholic", "roman_catholic"):
                return "костёл"
            return "церковь"
        return "архитектура"
    if building == "castle":
        return "замок"

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


def service_name(tags: dict[str, str], category: str) -> str | None:
    """Name: ``name`` else ``name:ru``.
    Toilets and transport stops fall back to «Туалет» / «Остановка»; others dropped.
    """
    name = tags.get("name") or tags.get("name:ru")
    if name:
        return name.strip()
    if category in ("туалет", "остановка транспорта"):
        label = "Туалет" if category == "туалет" else "Остановка"
        street = (tags.get("addr:street") or "").strip()
        return f"{label} ({street})" if street else label
    return None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Metres between two lat/lon points (local copy: imports no seed.* module)."""
    earth_radius_m = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return earth_radius_m * 2 * math.asin(math.sqrt(a))


def is_name_dup(
    row: dict[str, Any], existing: list[dict[str, Any]], threshold_m: float = DEDUP_RADIUS_M
) -> bool:
    """True when an existing place shares the exact name within threshold_m."""
    for ex in existing:
        if ex["name"] == row["name"] and _haversine_m(
            float(row["lat"]), float(row["lon"]), float(ex["lat"]), float(ex["lon"])
        ) < threshold_m:
            return True
    return False


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
        "ticket_price": tags.get("charge") or tags.get("fee", ""),
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
        "ticket_price": tags.get("charge") or tags.get("fee", ""),
        "visit_minutes": None,
        "links": "[]",
        "source_url": f"{SOURCE_PREFIX}{osm_type}/{osm_id}",
    }

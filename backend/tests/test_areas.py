"""Areas: one registry, one predicate, honest about missing geometry.

Covers data/areas.json and agent/areas.py (with agent.geofence delegation).
No network — polygons are the committed border files in backend/data/.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import areas
from agent.areas import (
    area_contains,
    area_lookup_by_slug,
    in_project_area,
    load_areas,
    resolve_area,
)
from agent.geofence import inside_belarus, inside_project_area

# Known landmarks from the committed data and previous geofence tests.
CITY = ("Гродно (центр)", 53.6772, 23.8232)
OBLAST_OUTSIDE_CITY = ("Новогрудок", 53.5941, 25.8249)
FOREIGN = [
    ("Вильнюс (Башня Гедимина)", 54.6868, 25.2907),
    ("Вильнюс (центр)", 54.6872, 25.2797),
    ("Тракайский замок", 54.6463, 24.9369),
    ("Белосток", 53.1325, 23.1688),
    ("Минск", 53.9006, 27.5590),
    ("Барановичи", 53.1307, 26.0139),
    ("Вилейка", 54.4903, 26.9107),
]


# registry integrity


def _document() -> dict:
    return json.loads(areas.AREAS_PATH.read_text(encoding="utf-8"))


def test_registry_is_versioned_with_unique_slugs():
    doc = _document()
    assert isinstance(doc["version"], int) and doc["version"] >= 1
    slugs = [a["slug"] for a in doc["areas"]]
    assert len(slugs) == len(set(slugs))


def test_every_area_has_ru_and_en_names():
    for area in load_areas().values():
        assert area["name_ru"].strip(), area["slug"]
        assert area["name_en"].strip(), area["slug"]


def test_duplicate_slug_is_rejected(monkeypatch):
    doc = _document()
    bad = {"areas": [doc["areas"][0], dict(doc["areas"][0])]}
    monkeypatch.setattr(areas, "_areas_document", lambda: bad)
    areas.load_areas.cache_clear()
    with pytest.raises(ValueError, match="duplicate slug"):
        areas.load_areas()


def test_duplicate_alias_across_areas_is_rejected(monkeypatch):
    bad = {
        "areas": [
            {
                "slug": "area-one",
                "name_ru": "Раз",
                "name_en": "One",
                "aliases": {"ru": ["общий"], "en": []},
            },
            {
                "slug": "area-two",
                "name_ru": "Два",
                "name_en": "Two",
                "aliases": {"ru": ["общий"], "en": []},
            },
        ]
    }
    monkeypatch.setattr(areas, "_areas_document", lambda: bad)
    areas.load_areas.cache_clear()
    with pytest.raises(ValueError, match="claimed by both"):
        areas.load_areas()


# honesty of the geometry data


def test_every_geometry_is_derivable_from_backend_data():
    """Geometry exists only where backend/data/ actually holds a border file;
    everything else is an explicit null, never invented coordinates."""
    doc = _document()
    with_geometry = [a for a in doc["areas"] if a.get("geometry")]
    without_geometry = [a for a in doc["areas"] if not a.get("geometry")]
    assert [a["slug"] for a in with_geometry] == [areas.PROJECT_AREA_SLUG]
    for area in with_geometry:
        source = areas.DATA_DIR / area["geometry"]["source"]
        assert source.exists(), source
        meta = json.loads(source.read_text(encoding="utf-8"))
        assert area["geometry"]["ring_count"] == len(meta["rings"])
        assert area["geometry"]["point_count"] == sum(len(r) for r in meta["rings"])
        assert area["geometry"]["license"]
        assert area["geometry"]["attribution"]
    assert without_geometry  # the geometry-less set is non-empty and documented
    for area in without_geometry:
        assert area["geometry"] is None
        assert area["geometry_note"]


def test_district_areas_match_places_region_csv():
    """District areas are sourced from the CSV's district column verbatim —
    no district invented, none missed."""
    lines = (areas.DATA_DIR / "places_region.csv").read_text(encoding="utf-8").splitlines()
    header = next(line for line in lines if line.startswith("# name|"))
    di = header.lstrip("# ").split("|").index("district")
    rows = (line.split("|") for line in lines if line and not line.startswith("#"))
    districts = {r[di] for r in rows if len(r) > di and r[di]}
    district_slugs = {a["slug"] for a in load_areas().values() if a["kind"] == "district"}
    assert len(district_slugs) == len(districts)
    for name in districts:
        assert resolve_area(name, "ru") in district_slugs, name


# resolve_area


def test_resolve_ru_and_en_aliases():
    cases = [
        ("старый город", "ru", "grodno-old-town"),
        ("Старый Гродно", "ru", "grodno-old-town"),
        ("Старый город Гродно", "ru", "grodno-old-town"),
        ("Old Town", "en", "grodno-old-town"),
        ("Old Grodno", "en", "grodno-old-town"),
        ("гродно", "ru", "grodno-city"),
        ("Город Гродно", "ru", "grodno-city"),
        ("Гродно (город)", "ru", "grodno-city"),
        ("Grodno", "en", "grodno-city"),
        ("Grodno City", "en", "grodno-city"),
        ("Гродненская область", "ru", "grodno-oblast"),
        ("Гродненской области", "ru", "grodno-oblast"),
        ("Grodno Region", "en", "grodno-oblast"),
        ("Grodno Oblast", "en", "grodno-oblast"),
        ("Лидский район", "ru", "lida-district"),
        ("Лидский", "ru", "lida-district"),
        ("Slonim District", "en", "slonim-district"),
        ("Novogrudok District", "en", "novogrudok-district"),
    ]
    for term, locale, expected in cases:
        assert resolve_area(term, locale) == expected, (term, locale)


def test_resolve_area_locale_fallback():
    assert resolve_area("старый город", "en") == "grodno-old-town"
    assert resolve_area("Old Town", "ru") == "grodno-old-town"
    assert resolve_area("Гродненская область", None) == "grodno-oblast"


def test_resolve_unknown_term_returns_none():
    """Unknown terms are None — never a silent default area."""
    cases = [
        ("Минск", "ru"),
        ("Вильнюс", "en"),
        ("Брест", "ru"),
        ("старый город 2", "ru"),
        ("", "ru"),
        ("   ", "ru"),
        ("!!!", "ru"),
        (None, "ru"),
    ]
    for term, locale in cases:
        assert resolve_area(term, locale) is None, (term, locale)


# area_contains


def test_area_contains_grodno_oblast():
    assert area_contains("grodno-oblast", CITY[1], CITY[2]) is True
    assert area_contains("grodno-oblast", OBLAST_OUTSIDE_CITY[1], OBLAST_OUTSIDE_CITY[2]) is True
    for name, lat, lon in FOREIGN:
        assert area_contains("grodno-oblast", lat, lon) is False, name


def test_area_contains_without_geometry_is_none_not_false():
    """grodno-city / grodno-old-town / districts have no boundary polygon in
    backend/data/: containment is unknown (None), never a silent False that
    would quietly filter out real places."""
    assert area_contains("grodno-city", CITY[1], CITY[2]) is None
    assert area_contains("grodno-old-town", CITY[1], CITY[2]) is None
    assert area_contains("lida-district", 53.8833, 25.2997) is None


def test_area_contains_unknown_slug_or_missing_coordinates():
    assert area_contains("minsk-oblast", 53.9, 27.5) is None
    assert area_contains(None, CITY[1], CITY[2]) is None
    assert area_contains("grodno-oblast", None, CITY[2]) is None
    assert area_contains("grodno-oblast", CITY[1], None) is None


# in_project_area: the one shared predicate


def test_point_inside_grodno_city_is_inside_project_area():
    # No city boundary polygon exists in backend/data/, so containment is
    # asserted at the project-area level: the predicate must not over-reject
    # the very city the areas are anchored to.
    assert in_project_area(CITY[1], CITY[2]) is True


def test_point_inside_oblast_outside_city_is_inside_project_area():
    # ~100 km from Grodno: inside the oblast, outside any city boundary.
    assert in_project_area(OBLAST_OUTSIDE_CITY[1], OBLAST_OUTSIDE_CITY[2]) is True


def test_vilnius_and_other_foreign_points_are_rejected():
    for name, lat, lon in FOREIGN:
        assert in_project_area(lat, lon) is False, name


def test_none_coordinates_are_rejected():
    assert in_project_area(None, None) is False
    assert in_project_area(CITY[1], None) is False
    assert in_project_area(None, CITY[2]) is False


def test_documented_boundary_exception_behaves_exactly_as_data_says():
    """belarus_border_keep.json documents POIs that ARE in Belarus but fall
    outside the 10m-simplified country polygon; they are treated as inside the
    project area. The predicate must agree with the documented data."""
    area = area_lookup_by_slug("grodno-oblast")
    by_name = {exc["name"]: exc for exc in area["exceptions"]}
    vor = by_name["Костёл Пресвятой Троицы (Вороново)"]
    assert (vor["lat"], vor["lon"]) == (54.1330, 25.0660)
    assert vor["radius_m"] == 300.0
    assert vor["source"] == "belarus_border_keep.json"
    assert inside_belarus(vor["lat"], vor["lon"]) is False  # outside the polygon
    assert in_project_area(vor["lat"], vor["lon"]) is True  # kept as inside
    for exc in area["exceptions"]:
        assert inside_belarus(exc["lat"], exc["lon"]) is False, exc["name"]
        assert in_project_area(exc["lat"], exc["lon"]) is True, exc["name"]


# area_lookup_by_slug


def test_area_lookup_by_slug_resolves_geometry_and_exceptions():
    area = area_lookup_by_slug("grodno-oblast")
    assert area["geometry"]["source"] == "grodno_border.json"
    assert area["geometry"]["ring_count"] == 1
    assert len(area["geometry"]["rings"]) == 1
    assert len(area["geometry"]["rings"][0]) == 1091
    assert area["geometry"]["license"] == "Creative Commons Attribution 3.0 License"
    assert len(area["exceptions"]) == 6


def test_area_lookup_by_slug_for_geometry_less_area():
    area = area_lookup_by_slug("grodno-city")
    assert area["geometry"] is None
    assert area["geometry_note"]
    assert area_lookup_by_slug("no-such-area") is None
    assert area_lookup_by_slug(None) is None


# geofence delegation: one predicate, not a copy


def test_geofence_delegates_to_the_single_areas_predicate():
    points = [CITY, OBLAST_OUTSIDE_CITY, *FOREIGN]
    for name, lat, lon in points:
        assert inside_project_area(lat, lon) == areas.in_project_area(lat, lon), name


def test_delegation_is_live(monkeypatch):
    """agent.geofence.inside_project_area must track agent.areas.in_project_area
    at call time — proving a single shared predicate, not a second copy."""
    monkeypatch.setattr(areas, "in_project_area", lambda lat, lon: True)
    assert inside_project_area(0.0, 0.0) is True
    monkeypatch.setattr(areas, "in_project_area", lambda lat, lon: False)
    assert inside_project_area(*CITY[1:]) is False

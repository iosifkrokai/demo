"""ingest_poi: tag mapping, name fallbacks, geofence and 150 m de-dup.

No network and no DB — pure helpers from scripts/ingest_poi.py.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from ingest_poi import (
    extract_name,
    is_name_dup,
    osm_element_to_row,
    tag_to_category,
)


def test_tag_to_category_mapping():
    assert tag_to_category({"amenity": "cafe"}) == "кафе"
    assert tag_to_category({"amenity": "fast_food"}) == "кафе"
    assert tag_to_category({"amenity": "restaurant"}) == "ресторан"
    assert tag_to_category({"amenity": "toilets"}) == "туалет"
    assert tag_to_category({"tourism": "hotel"}) == "гостиница"
    assert tag_to_category({"tourism": "hostel"}) == "гостиница"
    assert tag_to_category({"tourism": "guest_house"}) == "гостиница"
    assert tag_to_category({"amenity": "bench"}) is None


def test_name_prefers_name_then_name_ru():
    assert extract_name({"name": "Кафе А"}, "кафе") == "Кафе А"  # noqa: RUF001
    assert extract_name({"name:ru": "Кафе Б"}, "кафе") == "Кафе Б"


def test_unnamed_toilet_gets_fallback_name():
    assert extract_name({"amenity": "toilets"}, "туалет") == "Туалет"
    assert extract_name({"addr:street": "ул. Советская"}, "туалет") == "Туалет (ул. Советская)"
    # Everything else without a name is dropped.
    assert extract_name({"amenity": "cafe"}, "кафе") is None


def test_osm_element_to_row_rejects_foreign_point():
    el = {"type": "node", "id": 1, "lat": 54.6872, "lon": 25.2797,
          "tags": {"name": "Cafe Vilnius", "amenity": "cafe"}}
    assert osm_element_to_row(el) is None


def test_osm_element_to_row_accepts_grodno_point():
    el = {"type": "node", "id": 2, "lat": 53.6772, "lon": 23.8232,
          "tags": {"name": "Кафе Ласточка", "amenity": "cafe", "addr:city": "Гродно"}}
    row = osm_element_to_row(el)
    assert row is not None
    assert row["category"] == "кафе"
    assert row["source_url"] == "osm_poi:node/2"


def test_is_name_dup_within_150m():
    row = {"name": "Кафе X", "lat": 53.6772, "lon": 23.8232}
    same_name_near = [{"name": "Кафе X", "lat": 53.6782, "lon": 23.8232}]  # ~111 m
    other_name_near = [{"name": "Кафе Y", "lat": 53.6782, "lon": 23.8232}]
    same_name_far = [{"name": "Кафе X", "lat": 53.7000, "lon": 23.8232}]  # ~2.5 km
    assert is_name_dup(row, same_name_near) is True
    assert is_name_dup(row, other_name_near) is False
    assert is_name_dup(row, same_name_far) is False

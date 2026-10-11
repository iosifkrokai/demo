"""Country geofence: the bbox ingest must not leak Vilnius/Poland POIs.

No network — the border polygon is the committed data/geo/belarus_border.json.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reference import areas
from reference.geofence import inside_belarus, inside_project_area

INSIDE = [
    ("Гродно", 53.6772, 23.8232),
    ("Новогрудок", 53.5941, 25.8249),
    ("Мир", 53.4541, 26.4726),
    ("Лида", 53.8833, 25.2997),
    ("Ошмяны", 54.4243, 25.9375),
    ("Минск", 53.9006, 27.5590),
    ("Брест", 52.0976, 23.7341),
]

OUTSIDE = [
    ("Вильнюс (Башня Гедимина)", 54.6868, 25.2907),
    ("Вильнюс (центр)", 54.6872, 25.2797),
    ("Тракайский замок", 54.6463, 24.9369),
    ("Белосток", 53.1325, 23.1688),
    ("Друскининкай", 54.0167, 23.9667),
    ("Варшава", 52.2297, 21.0122),
]


def test_belarus_landmarks_are_inside():
    for name, lat, lon in INSIDE:
        assert inside_belarus(lat, lon), f"{name} should be inside Belarus"


def test_foreign_landmarks_are_outside():
    for name, lat, lon in OUTSIDE:
        assert not inside_belarus(lat, lon), f"{name} must not be inside Belarus"


def test_border_keep_list_covers_verified_belarusian_pois():
    """The 10m polygon shaves a few Belarusian POIs off the border; they are kept
    explicitly after Nominatim verification (data/geo/belarus_border_keep.json)."""
    kept = [
        ("Костёл Пресвятой Троицы (Вороново)", 54.1330, 25.0660),
        ("Старый мост (разрушен)", 53.9114, 23.6292),
        ("Водяная мельница", 54.2622, 25.3602),
    ]
    for name, lat, lon in kept:
        assert not inside_belarus(lat, lon), f"{name}: expected outside the polygon"
        assert inside_project_area(lat, lon), f"{name} should still count as project area"


def test_project_area_still_rejects_foreign_points():
    for name, lat, lon in OUTSIDE:
        assert not inside_project_area(lat, lon), f"{name} must stay outside"


def test_project_area_predicate_is_delegated_to_areas_module():
    """inside_project_area must be reference.areas.in_project_area's single shared
    predicate (one implementation for import and query), not a second copy."""
    for name, lat, lon in INSIDE:
        assert inside_project_area(lat, lon) == areas.in_project_area(lat, lon), name
    for name, lat, lon in OUTSIDE:
        assert inside_project_area(lat, lon) == areas.in_project_area(lat, lon), name

"""Secondary points measured along a route line."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.store import services
from db.store.places import PostgresPlaceRepository
from reference.taxonomy import all_categories

CENTRE_LINE = {
    "type": "LineString",
    "coordinates": [[23.8180, 53.6775], [23.8250, 53.6785], [23.8330, 53.6800]],
}


def _row(**over: object) -> dict:
    base = {
        "id": 1,
        "source_url": "osm:node/1",
        "name": "Кафе",
        "category": "кафе",
        "town": "Гродно",
        "lat": 53.68,
        "lon": 23.82,
        "opening_hours": "ежедневно 9:00–19:00",
        "off_line_m": 42.4,
        "along_fraction": 0.25,
    }
    base.update(over)
    return base


def test_service_codes_come_from_the_taxonomy():
    codes = services.service_codes()
    assert codes, "в таксономии должны быть услуги"
    assert set(codes) == {
        cat.code for cat in all_categories() if getattr(cat, "role", None) == "service"
    }


def test_a_castle_is_never_a_service():
    assert services.service_codes(["замок", "музей"]) == []


def test_unknown_codes_are_dropped_not_trusted():
    assert services.service_codes(["кафе", "выдуманная_категория"]) == ["кафе"]


def test_asking_for_nothing_means_every_service():
    assert services.service_codes(None) == services.service_codes()


@pytest.mark.parametrize(
    "shape,reason",
    [
        (None, "shape_not_linestring"),
        ({"type": "Point", "coordinates": [23.8, 53.6]}, "shape_not_linestring"),
        ({"type": "LineString", "coordinates": [[23.8, 53.6]]}, "shape_needs_two_points"),
        ({"type": "LineString", "coordinates": [[23.8, 53.6], ["x", 53.6]]}, "shape_bad_point"),
        (
            {
                "type": "LineString",
                "coordinates": [[23.8, 53.6]] * (services.MAX_LINE_POINTS + 1),
            },
            "shape_too_long",
        ),
    ],
)
def test_a_shape_that_cannot_be_measured_is_refused(shape, reason):
    with pytest.raises(ValueError) as exc:
        services.route_line(shape)
    assert str(exc.value) == reason


def test_a_real_line_passes_through_unchanged():
    assert services.route_line(CENTRE_LINE) is CENTRE_LINE


def test_the_answer_never_calls_distance_a_detour():
    item = services.item_of(_row(), 1000.0)
    assert item["off_line_m"] == 42
    assert item["detour_confirmed"] is False


def test_position_along_the_line_comes_from_the_fraction():
    item = services.item_of(_row(along_fraction=0.25), 4000.0)
    assert item["along_m"] == 1000


def test_unknown_hours_are_quoted_as_unknown():
    item = services.item_of(_row(opening_hours="  "), 1000.0)
    assert item["opening_hours"] is None
    assert item["hours_known"] is False


def test_known_hours_are_passed_through_untouched():
    item = services.item_of(_row(opening_hours="ежедневно 9:00–19:00"), 1000.0)
    assert item["opening_hours"] == "ежедневно 9:00–19:00"
    assert item["hours_known"] is True


def test_a_driver_passes_more_services_than_a_walker():
    assert services.MAX_OFF_LINE_M["car"] > services.MAX_OFF_LINE_M["pedestrian"]
    assert services.DEFAULT_PROFILE == "pedestrian"


def test_valhalla_profiles_map_onto_walking_thresholds():
    assert services.threshold_for("auto") == services.MAX_OFF_LINE_M["car"]
    assert services.threshold_for("truck") == services.MAX_OFF_LINE_M["car"]
    assert services.threshold_for("bicycle") == services.MAX_OFF_LINE_M["bicycle"]
    assert services.threshold_for("pedestrian", 42.0) == 42.0
    assert services.threshold_for("самокат") == services.MAX_OFF_LINE_M["pedestrian"]


@pytest.mark.skipif(
    os.environ.get("SMOKE_SKIP_LIVE") == "1", reason="live-проверки отключены"
)
def test_live_services_along_a_real_street_in_grodno():
    """A walk down Советская must find real cafés and toilets beside the line."""
    try:
        from db.store.clients_store import default_connect

        conn = default_connect()
    except Exception as exc:
        pytest.skip(f"нет базы: {exc}")

    try:
        places = PostgresPlaceRepository(connect=lambda: conn)
        answer = places.services_along(CENTRE_LINE, profile="pedestrian", limit=8)
    finally:
        conn.close()

    assert answer["measured"] == "distance_to_line"
    assert answer["detour_confirmed"] is False
    assert answer["line_m"] > 0
    assert answer["items"], "в центре Гродно услуги обязаны найтись"

    codes = set(services.service_codes())
    for item in answer["items"]:
        assert item["category"] in codes, item
        assert 0 <= item["off_line_m"] <= answer["max_off_line_m"], item
        assert 0 <= item["along_fraction"] <= 1, item
        assert item["detour_confirmed"] is False

    along = [item["along_m"] for item in answer["items"]]
    assert along == sorted(along), "услуги идут вдоль маршрута, а не вперемешку"
    assert max(along) > 0, "позиция вдоль маршрута должна быть измерена, а не нулевой"

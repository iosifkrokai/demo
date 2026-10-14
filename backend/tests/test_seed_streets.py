"""Street hints: which road a point stands on, and when the answer is not guessed."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

LAT, LON = 53.6778, 23.8295
_M_PER_DEG = 111_320.0


def _north(metres: float) -> float:
    """The latitude ``metres`` north of the test road."""
    return LAT + metres / _M_PER_DEG


def _east_west(name: str, *, at_lat: float = LAT, named: bool = True) -> Any:
    """A road running east-west, so a point due north of it stands ``metres`` away."""
    from db.seed.streets import Road

    return Road(name=name, named=named, points=((at_lat, LON), (at_lat, LON + 0.002)))


def test_a_point_on_one_named_road_gets_that_street() -> None:
    from db.seed.streets import answer_for

    answer = answer_for([_east_west("Советская улица")], _north(10), LON + 0.001)

    assert answer.status == "ok"
    assert answer.street == "Советская улица"
    assert answer.dist_m == pytest.approx(10, abs=0.5)


def test_a_second_differently_named_road_nearby_is_ambiguous() -> None:
    from db.seed.streets import answer_for

    roads = [_east_west("Советская улица"), _east_west("Мира улица", at_lat=_north(30))]
    answer = answer_for(roads, _north(10), LON + 0.001)

    assert answer.status == "ambiguous"
    assert answer.street == "Советская улица"
    assert answer.rival is not None
    assert answer.rival[0] == "Мира улица"


def test_the_same_street_mapped_twice_is_not_ambiguous() -> None:
    """A street split into two ways still answers with one name."""
    from db.seed.streets import answer_for

    roads = [
        _east_west("Советская улица"),
        _east_west("Советская улица", at_lat=_north(30)),
    ]
    answer = answer_for(roads, _north(10), LON + 0.001)

    assert answer.status == "ok"


def test_a_road_number_instead_of_a_name_is_refused() -> None:
    from db.seed.streets import answer_for

    answer = answer_for([_east_west("Р42", named=False)], _north(10), LON + 0.001)

    assert answer.status == "number"
    assert answer.street == "Р42"


def test_a_number_road_between_the_point_and_a_named_street_wins() -> None:
    """A stop that sits on a numbered road is not named after the street behind it."""
    from db.seed.streets import answer_for

    roads = [
        _east_west("Р42", at_lat=_north(5), named=False),
        _east_west("Советская улица", at_lat=LAT),
    ]
    answer = answer_for(roads, _north(10), LON + 0.001)

    assert answer.status == "number"


def test_a_road_beyond_the_threshold_is_nothing() -> None:
    from db.seed.streets import answer_for

    answer = answer_for([_east_west("Советская улица")], _north(111), LON + 0.001)

    assert answer.status == "none"
    assert answer.street is None


def test_the_answer_does_not_depend_on_the_order_of_the_roads() -> None:
    from db.seed.streets import answer_for

    roads = [_east_west("Советская улица"), _east_west("Мира улица", at_lat=_north(30))]

    first = answer_for(roads, _north(10), LON + 0.001)
    second = answer_for(list(reversed(roads)), _north(10), LON + 0.001)

    assert first == second


def test_the_road_number_detector_reads_a_number_not_a_street() -> None:
    from db.seed.streets import looks_like_road_number

    for label in ("М6", "Р42", "Н6131", "МКАД-2"):
        assert looks_like_road_number(label), label
    for label in ("Советская улица", "улица 1 Мая", "2-я Магистральная улица"):
        assert not looks_like_road_number(label), label


def test_a_road_name_prefers_russian() -> None:
    from db.seed.streets import pick_road_name

    assert pick_road_name({"name": "вуліца Савецкая", "name:ru": "улица Советская"}) == (
        "улица Советская"
    )
    assert pick_road_name({"name": "улица Советская"}) == "улица Советская"
    assert pick_road_name({"name:be": "вуліца Савецкая"}) == "вуліца Савецкая"
    assert pick_road_name({}) is None


def test_the_targets_are_the_labels_without_a_hint() -> None:
    from db.seed.streets import collect_targets

    records = [
        {"source_url": "osm_poi:node/1", "name": "Остановка", "category": "остановка транспорта"},
        {"source_url": "osm_poi:node/2", "name": "Туалет", "category": "туалет"},
        {
            "source_url": "osm_poi:node/3",
            "name": "Остановка (ул. X)",
            "category": "остановка транспорта",
        },
        {
            "source_url": "osm_poi:node/4",
            "name": "Солы (па патрабаванню)",
            "category": "остановка транспорта",
        },
        {"source_url": "osm_poi:node/5", "name": "Автовокзал", "category": "остановка транспорта"},
        {"source_url": "osm_poi:node/6", "name": "Остановка", "category": "кафе"},
    ]
    records = [dict(r, lat=LAT, lon=LON) for r in records]

    kept = collect_targets(records)

    assert [r["source_url"] for r in kept] == ["osm_poi:node/1", "osm_poi:node/2"]


def test_the_index_finds_the_roads_around_a_point() -> None:
    from db.seed.streets import StreetIndex

    roads = [_east_west("Советская улица"), _east_west("Мира улица", at_lat=54.0)]
    index = StreetIndex(roads)

    found = index.candidates(LAT, LON)

    assert [road.name for road in found] == ["Советская улица"]


def test_the_written_document_reads_back_as_street_entries(tmp_path: Path) -> None:
    from db.seed.streets import read_place_street_entries, write_document

    entries = [
        {
            "source_url": "osm_poi:node/1",
            "name": "Остановка",
            "street": "Советская улица",
            "dist_m": 8.0,
            "source": "osm_street",
        }
    ]
    path = tmp_path / "place_streets.json"

    write_document(path, entries)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert "description" in document
    assert read_place_street_entries(path) == entries


@pytest.fixture
def conn() -> Iterator[Any]:
    """A connection to the real schema, or a skip. Everything it does is rolled back."""
    from db.seed import pipeline
    from db.seed.cli import DEFAULT_DSN

    try:
        connection = pipeline.connect(os.environ.get("DATABASE_URL", DEFAULT_DSN))
    except Exception as exc:
        pytest.skip(f"database not reachable: {type(exc).__name__}")
    yield connection
    connection.rollback()
    connection.close()


def _place(conn: Any, source_url: str, name: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO places (name, category, category_source, lat, lon, source_url)
            VALUES (%s, 'остановка транспорта', 'auto', %s, %s, %s)
            RETURNING id
            """,
            (name, LAT, LON, source_url),
        )
        return int(cur.fetchone()[0])


def _name_of(conn: Any, place_id: int) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM places WHERE id = %s", (place_id,))
        return str(cur.fetchone()[0])


def test_applying_the_file_extends_the_label_and_leaves_a_real_name(
    conn: Any, tmp_path: Path
) -> None:
    from db.seed import pipeline
    from db.seed.streets import write_document

    key = f"test:streets/{uuid4().hex}"
    real = f"test:streets/{uuid4().hex}"
    place_id = _place(conn, key, "Остановка")
    real_id = _place(conn, real, "Автовокзал")
    path = tmp_path / "place_streets.json"
    write_document(
        path,
        [
            {
                "source_url": key,
                "name": "Остановка",
                "street": "Советская улица",
                "dist_m": 8.0,
                "source": "osm_street",
            },
            {
                "source_url": real,
                "name": "Остановка",
                "street": "Мира улица",
                "dist_m": 9.0,
                "source": "osm_street",
            },
        ],
    )

    stats = pipeline.apply_place_streets(conn, path)

    assert stats["written"] == 1
    assert _name_of(conn, place_id) == "Остановка (Советская улица)"
    assert _name_of(conn, real_id) == "Автовокзал"


def test_applying_the_file_twice_changes_nothing_the_second_time(conn: Any, tmp_path: Path) -> None:
    from db.seed import pipeline
    from db.seed.streets import write_document

    key = f"test:streets/{uuid4().hex}"
    _place(conn, key, "Остановка")
    path = tmp_path / "place_streets.json"
    write_document(
        path,
        [
            {
                "source_url": key,
                "name": "Остановка",
                "street": "Советская улица",
                "dist_m": 8.0,
                "source": "osm_street",
            }
        ],
    )

    pipeline.apply_place_streets(conn, path)
    second = pipeline.apply_place_streets(conn, path)

    assert second["written"] == 0

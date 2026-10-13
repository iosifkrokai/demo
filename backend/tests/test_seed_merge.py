"""The records the projection drops: a hand-authored twin, and one object mapped twice."""

from __future__ import annotations

from typing import Any

HAND: dict[str, str] = {"_category_source": "dataset", "_dataset": "region"}
AUTO: dict[str, str] = {"_category_source": "auto", "_dataset": "osm_poi"}


def _record(
    name: str,
    lat: float,
    lon: float,
    source_url: str,
    kind: dict[str, str],
    category: str = "остановка транспорта",
    **fields: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "lat": lat,
        "lon": lon,
        "source_url": source_url,
        "category": category,
        **fields,
        **kind,
    }


def test_a_hand_authored_row_shadows_its_automatic_twin() -> None:
    from db.seed.datasets import resolve_shadowed

    hand = _record("Мирский замок", 53.4517, 26.4736, "region:mir-castle", HAND)
    auto = _record("Мирский Замок", 53.451193, 26.472922, "osm:relation/1579104", AUTO)

    kept, shadowed = resolve_shadowed([hand, auto])

    assert kept == [hand]
    assert [e["source_url"] for e in shadowed] == ["osm:relation/1579104"]
    assert shadowed[0]["shadowed_by"] == "region:mir-castle"
    assert 70 < shadowed[0]["distance_m"] < 75


def test_the_rule_ignores_two_automatic_rows() -> None:
    from db.seed.datasets import resolve_shadowed

    a = _record("Мирский замок", 53.4517, 26.4736, "osm:a", AUTO)
    b = _record("Мирский Замок", 53.451193, 26.472922, "osm:b", AUTO)

    kept, shadowed = resolve_shadowed([a, b])

    assert kept == [a, b]
    assert shadowed == []


def test_the_rule_ignores_a_name_beyond_the_radius() -> None:
    from db.seed.datasets import resolve_shadowed

    hand = _record("Часовня", 53.4517, 26.4736, "region:chapel", HAND)
    auto = _record("Часовня", 53.4700, 26.4736, "osm:chapel", AUTO)

    kept, shadowed = resolve_shadowed([hand, auto])

    assert kept == [hand, auto]
    assert shadowed == []


def test_the_rule_does_not_fire_between_hand_authored_rows() -> None:
    from db.seed.datasets import resolve_shadowed

    region = _record("Любчанский замок", 53.75218, 26.06926, "region:lyubcha-castle", HAND)
    city = _record("Любчанский замок", 53.75220, 26.06930, "city:lyubcha-castle", HAND)

    kept, shadowed = resolve_shadowed([region, city])

    assert kept == [region, city]
    assert shadowed == []


def test_a_record_without_coordinates_is_never_shadowed() -> None:
    from db.seed.datasets import resolve_shadowed

    hand = _record("Часовня", 53.4517, 26.4736, "region:chapel", HAND)
    auto = {"name": "Часовня", "lat": None, "lon": None, "source_url": "osm:chapel", **AUTO}

    kept, shadowed = resolve_shadowed([hand, auto])

    assert kept == [hand, auto]
    assert shadowed == []


def test_untouched_records_keep_their_order_and_identity() -> None:
    from db.seed.datasets import resolve_shadowed

    first = _record("Аптека-музей", 53.6785, 23.8311, "city:pharmacy", HAND)
    hand = _record("Мирский замок", 53.4517, 26.4736, "region:mir-castle", HAND)
    last = _record("Коложская церковь", 53.6785, 23.8311, "city:kolozha", HAND)
    auto = _record("Мирский Замок", 53.451193, 26.472922, "osm:relation/1579104", AUTO)

    kept, shadowed = resolve_shadowed([first, auto, hand, last])

    assert kept == [first, hand, last]
    assert kept[0] is first
    assert kept[2] is last
    assert len(shadowed) == 1


def test_the_committed_files_merge_exactly_these_pairs() -> None:
    from db.seed.datasets import collect_records, default_datasets, resolve_shadowed

    collected = collect_records(default_datasets())
    kept, shadowed = resolve_shadowed(collected["records"])

    assert {(e["source_url"], e["shadowed_by"]) for e in shadowed} == {
        ("osm:relation/1579104", "region:mir-castle"),
        ("osm:way/243087446", "region:lyubcha-castle"),
        ("osm:way/29125014", "city:choral-synagogue"),
    }
    assert len(kept) == len(collected["records"]) - 3


def test_one_object_mapped_twice_collapses_to_one_row() -> None:
    from db.seed.datasets import resolve_twins

    node = _record("Вострава", 53.6770, 23.8300, "osm_poi:node/1", AUTO)
    way = _record("Вострава", 53.6770, 23.8300, "osm_poi:way/2", AUTO)

    kept, shadowed = resolve_twins([node, way])

    assert kept == [node]
    assert [e["source_url"] for e in shadowed] == ["osm_poi:way/2"]
    assert shadowed[0]["rule"] == "same object"


def test_the_richer_row_survives_the_collapse() -> None:
    from db.seed.datasets import resolve_twins

    bare = _record("Туалет", 53.6770, 23.8300, "osm_poi:node/1", AUTO)
    rich = _record("Туалет", 53.6770, 23.8300, "osm_poi:way/2", AUTO, opening_hours="9:00-18:00")

    kept, shadowed = resolve_twins([bare, rich])

    assert kept == [rich]
    assert [e["source_url"] for e in shadowed] == ["osm_poi:node/1"]


def test_a_namesake_a_hundred_metres_away_is_not_a_twin() -> None:
    """ДОТы и блиндажи стоят линией и зовутся одинаково — это разные объекты."""
    from db.seed.datasets import resolve_twins

    a = _record("ДОТ", 53.6770, 23.8300, "osm:node/1", AUTO, category="замок")
    b = _record("ДОТ", 53.6770, 23.8500, "osm:node/2", AUTO, category="замок")

    kept, shadowed = resolve_twins([a, b])

    assert kept == [a, b]
    assert shadowed == []


def test_two_categories_at_one_spot_are_left_alone() -> None:
    """Отель и его кафе — две функции одного здания, а не дубль."""
    from db.seed.datasets import resolve_twins

    cafe = _record("У Груши", 53.6770, 23.8300, "osm_poi:node/1", AUTO, category="кафе")
    hotel = _record("У Груши", 53.6770, 23.8300, "osm_poi:node/2", AUTO, category="гостиница")

    kept, shadowed = resolve_twins([cafe, hotel])

    assert kept == [cafe, hotel]
    assert shadowed == []


def test_the_collapse_never_touches_hand_authored_rows() -> None:
    from db.seed.datasets import resolve_twins

    region = _record("Любчанский замок", 53.7522, 26.0693, "region:lyubcha-castle", HAND)
    city = _record("Любчанский замок", 53.7522, 26.0693, "city:lyubcha-castle", HAND)

    kept, shadowed = resolve_twins([region, city])

    assert kept == [region, city]
    assert shadowed == []


def test_the_committed_files_collapse_exactly_these_twins() -> None:
    from db.seed.datasets import TWIN_RADIUS_M, collect_records, default_datasets, resolve_twins

    collected = collect_records(default_datasets())
    _, twins = resolve_twins(collected["records"])

    assert len(twins) == 14
    assert all(entry["distance_m"] < TWIN_RADIUS_M for entry in twins)
    assert all(entry["rule"] == "same object" for entry in twins)
    assert {entry["name"] for entry in twins} == {
        "Остановка",
        "Туалет",
        "Вострава",
        "Наваспаск",
        "Шостакі",
        "Дзен",
    }

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
    from db.seed.datasets import (
        collect_records,
        default_datasets,
        load_place_aliases,
        resolve_shadowed,
    )

    collected = collect_records(default_datasets())
    kept, shadowed = resolve_shadowed(collected["records"], aliases=load_place_aliases())

    assert {(e["source_url"], e["shadowed_by"]) for e in shadowed} == {
        ("osm:relation/1579104", "region:mir-castle"),
        ("osm:node/10572808231", "region:mir-castle"),
        ("osm:node/10572806384", "region:mir-castle"),
        ("osm:way/243087446", "region:lyubcha-castle"),
        ("osm:way/29125014", "city:choral-synagogue"),
    }
    assert len(kept) == len(collected["records"]) - 5


def test_an_alias_shadows_the_automatic_row_that_answers_to_it() -> None:
    from db.seed.datasets import resolve_shadowed

    hand = _record("Мирский замок", 53.4517, 26.4736, "region:mir-castle", HAND)
    park = _record("Парк Мир", 53.451693, 26.472738, "osm:node/10572806384", AUTO)

    kept, shadowed = resolve_shadowed([hand, park], aliases={"region:mir-castle": {"парк мир"}})

    assert kept == [hand]
    assert [e["source_url"] for e in shadowed] == ["osm:node/10572806384"]
    assert shadowed[0]["rule"] == "hand-authored"


def test_an_alias_is_matched_as_a_name_is() -> None:
    from db.seed.datasets import resolve_shadowed

    hand = _record("Мирский замок", 53.4517, 26.4736, "region:mir-castle", HAND)
    museum = _record("ЗАМКОВЫЙ  комплекс — Мир", 53.45220, 26.47370, "osm:node/10572808231", AUTO)

    kept, shadowed = resolve_shadowed(
        [hand, museum], aliases={"region:mir-castle": {"замковый комплекс мир"}}
    )

    assert kept == [hand]
    assert len(shadowed) == 1


def test_an_alias_beyond_the_radius_shadows_nothing() -> None:
    from db.seed.datasets import resolve_shadowed

    hand = _record("Мирский замок", 53.4517, 26.4736, "region:mir-castle", HAND)
    park = _record("Парк Мир", 53.4600, 26.4736, "osm:node/far", AUTO)

    kept, shadowed = resolve_shadowed([hand, park], aliases={"region:mir-castle": {"парк мир"}})

    assert kept == [hand, park]
    assert shadowed == []


def test_the_committed_alias_file_names_the_mir_ensemble_and_not_the_hotel() -> None:
    from db.seed.datasets import load_place_aliases, read_place_alias_entries

    entries = read_place_alias_entries()
    by_name = {row["alias"]: row for row in entries}

    assert by_name["Парк Мир"]["source_url"] == "region:mir-castle"
    assert by_name["Парк Мир"]["locale"] == "ru"
    assert "Замковый комплекс Мир" in by_name
    assert "Мірскі замак" not in {alias.casefold() for alias in by_name}
    assert load_place_aliases()["region:mir-castle"] >= {"парк мир", "замковый комплекс мир"}


def test_a_declared_merge_collapses_the_pair_it_names() -> None:
    from db.seed.datasets import resolve_declared_merges

    way = _record(
        "Гродненский государственный историко-археологический музей",
        53.6770,
        23.8300,
        "osm:way/1102852544",
        AUTO,
        category="замок",
    )
    node = _record(
        "Гродненский государственный историко-археологический музей",
        53.6785,
        23.8300,
        "osm:node/4301841763",
        AUTO,
        category="музей",
    )

    kept, merged, stale = resolve_declared_merges(
        [way, node], [{"keep": "osm:node/4301841763", "drop": "osm:way/1102852544"}]
    )

    assert kept == [node]
    assert len(merged) == 1
    entry = merged[0]
    assert entry["name"] == "Гродненский государственный историко-археологический музей"
    assert entry["source_url"] == "osm:way/1102852544"
    assert entry["shadowed_by"] == "osm:node/4301841763"
    assert entry["rule"] == "declared"
    assert 160 < entry["distance_m"] < 175
    assert stale == []


def test_a_declared_merge_that_names_a_missing_row_is_reported_not_ignored() -> None:
    from db.seed.datasets import resolve_declared_merges

    node = _record("Музей", 53.6785, 23.8300, "osm:node/4301841763", AUTO, category="музей")

    kept, merged, stale = resolve_declared_merges(
        [node], [{"keep": "osm:node/4301841763", "drop": "osm:way/1102852544"}]
    )

    assert kept == [node]
    assert merged == []
    assert stale == ["osm:node/4301841763 ← osm:way/1102852544"]


def test_a_declared_merge_never_drops_a_hand_authored_row() -> None:
    from db.seed.datasets import resolve_declared_merges

    hand = _record("Мирский замок", 53.4517, 26.4736, "region:mir-castle", HAND)
    auto = _record("Мирский Замок", 53.451193, 26.472922, "osm:relation/1579104", AUTO)

    kept, merged, stale = resolve_declared_merges(
        [hand, auto], [{"keep": "osm:relation/1579104", "drop": "region:mir-castle"}]
    )

    assert kept == [hand, auto]
    assert merged == []
    assert stale == ["osm:relation/1579104 ← region:mir-castle (the row is hand-authored)"]


def test_an_undeclared_pair_of_namesakes_is_left_alone() -> None:
    """Distance is not a declaration: the doctrine names a pair or it stays."""
    from db.seed.datasets import resolve_declared_merges

    street = _record("Кирова", 53.6770, 23.8300, "osm:way/1", AUTO, category="улица")
    stop = _record("Кирова", 53.6771, 23.8301, "osm:node/2", AUTO, category="остановка")

    kept, merged, stale = resolve_declared_merges([street, stop], [])

    assert kept == [street, stop]
    assert merged == []
    assert stale == []


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
    """The collapse over the committed sets, pinning the count so a new rule shows up here.

    The 38 stairways are all «Лестница»: OSM splits one staircase into ways a metre apart
    (osm:way/606412524 and its neighbour 606412525 are 0.8 m, some pairs exactly 0.0 m),
    which the rule reads as the one object they are. The bound below is two radii, not one:
    a dropped row is measured to the row that kept it, which may be a neighbour of the
    group's head rather than the head itself.
    """
    from db.seed.datasets import TWIN_RADIUS_M, collect_records, default_datasets, resolve_twins

    collected = collect_records(default_datasets())
    _, twins = resolve_twins(collected["records"])

    assert len(twins) == 53
    assert all(entry["distance_m"] <= 2 * TWIN_RADIUS_M for entry in twins)
    assert all(entry["rule"] == "same object" for entry in twins)
    assert {entry["name"] for entry in twins} == {
        "Остановка",
        "Туалет",
        "Лестница",
        "Вострава",
        "Наваспаск",
        "Шостакі",
        "Дзен",
        "Фарный костёл Преображения Господнего",
    }


def test_the_committed_files_carry_no_excluded_names() -> None:
    """The names `fetch` refuses must not be in the files already committed.

    `seed fetch` drops them (db/seed/cli.py), so a name coming back would mean the
    file was regenerated before the guard, or edited past it.
    """
    from db.seed.datasets import (
        default_datasets,
        is_excluded_name,
        load_dataset,
        load_excluded_names,
    )

    excluded = load_excluded_names()
    assert excluded, "the exclusion list is empty — this test would pass vacuously"
    offenders = [
        (ds.name, row["name"])
        for ds in default_datasets()
        for row in load_dataset(ds)
        if is_excluded_name(row.get("name"), excluded)
    ]

    assert offenders == []

"""The ruins fall-through, the two new sights, and the stage that re-labels the rows."""

from __future__ import annotations

from typing import Any

GRODNO = (53.6778, 23.8295)
FAR_AWAY = (49.0, 20.0)


def _element(osm_id: int, lat: float, lon: float, **tags: str) -> dict[str, Any]:
    return {"type": "node", "id": osm_id, "center": {"lat": lat, "lon": lon}, "tags": tags}


def test_a_ruin_is_a_condition_and_does_not_type_the_object() -> None:
    """`historic=ruins` alone said `замок`, which put pillboxes and mills in the castle.

    A bare ruin, and a mill's ruin, fall through to the catch-all; a castle in ruins is
    still a castle, so `historic=castle` and `building=castle` keep it.
    """
    from db.seed.osm_tags import sight_tag_to_category

    assert sight_tag_to_category({"historic": "ruins"}) == "архитектура"
    assert (
        sight_tag_to_category({"historic": "ruins", "name": "Руины водяной мельницы"})
        == "архитектура"
    )
    assert sight_tag_to_category({"historic": "castle", "ruins": "yes"}) == "замок"
    assert sight_tag_to_category({"building": "castle", "historic": "ruins"}) == "замок"


def test_a_fortification_reads_from_its_tag_where_osm_states_one() -> None:
    from db.seed.osm_tags import sight_tag_to_category

    assert sight_tag_to_category({"military": "bunker"}) == "укрепление"
    assert sight_tag_to_category({"historic": "fort"}) == "укрепление"
    assert sight_tag_to_category({"historic": "ruins", "military": "bunker"}) == "укрепление"


def test_a_fortification_with_no_tag_reads_from_its_name() -> None:
    """OSM's fortification tagging is thin: the name is the only evidence the rest carry."""
    from db.seed.osm_tags import sight_tag_to_category

    for name in (
        "ДОТ Гродненского УР",
        "Немецкие блиндажи Первой мировой войны",
        "Форт № 2",
        "Немецкое убежище 1-й мировой войны",
        "Опорный пункт №16 Гродненской крепости",
        "Оборонительные сооружения 1-й мировой войны",
    ):
        assert sight_tag_to_category({"historic": "ruins", "name": name}) == "укрепление", name


def test_a_word_that_only_contains_a_fort_word_is_not_a_fort() -> None:
    from db.seed.osm_tags import sight_tag_to_category

    assert sight_tag_to_category({"historic": "castle", "name": "Фортуна"}) == "замок"
    assert sight_tag_to_category({"tourism": "attraction", "name": "Фортуна"}) == "архитектура"


def test_a_named_boulder_is_its_own_sight_but_a_memorial_stone_is_not() -> None:
    from db.seed.osm_tags import sight_tag_to_category

    assert sight_tag_to_category({"natural": "stone", "tourism": "attraction"}) == "валун"
    assert sight_tag_to_category({"natural": "rock"}) == "валун"
    assert sight_tag_to_category({"natural": "stone", "historic": "memorial"}) == "памятник"
    assert sight_tag_to_category({"natural": "spring"}) is None


def test_the_two_new_codes_are_sights_the_map_hides_by_default() -> None:
    from db.seed.taxonomy import read_categories

    categories = {c.code: c for c in read_categories()}
    for code, ru, en in (
        ("укрепление", "укрепление", "fortification"),
        ("валун", "валун", "boulder"),
    ):
        cat = categories[code]
        assert (cat.ru, cat.en, cat.role) == (ru, en, "sight")
        assert cat.highlight is False
    assert categories["укрепление"].osm_tags == ("military=bunker", "historic=fort")
    assert categories["валун"].osm_tags == ("natural=stone", "natural=rock")


def test_the_stage_splits_a_held_object_into_a_correction_and_a_new_one_into_a_row() -> None:
    """A row the snapshot holds is re-labelled; an object it never held becomes a row."""
    from db.seed.finds import split_objects

    held = {
        "osm:node/1": {"category": "замок", "_category_source": "auto"},
        "osm:node/2": {"category": "архитектура", "_category_source": "curated"},
    }
    rows, corrections = split_objects(
        [
            _element(1, *GRODNO, historic="ruins", name="Руины мельницы"),
            _element(2, *GRODNO, military="bunker", name="ДОТ"),
            _element(3, *GRODNO, natural="stone", name="Войстомский валун"),
            _element(4, *GRODNO, historic="memorial", name="Памятник"),
            _element(5, *FAR_AWAY, military="bunker", name="ДОТ далеко"),
        ],
        held,
    )

    assert corrections == [
        {
            "source_url": "osm:node/1",
            "category": "архитектура",
            "visit_minutes": 20,
        }
    ]
    assert [row["source_url"] for row in rows] == ["osm:node/3"]
    assert rows[0]["category"] == "валун"


def test_the_correction_leaves_a_curated_category_alone() -> None:
    from db.seed.finds import split_objects

    held = {"osm:node/2": {"category": "архитектура", "_category_source": "curated"}}
    rows, corrections = split_objects([_element(2, *GRODNO, military="bunker", name="ДОТ")], held)

    assert rows == []
    assert corrections == []


def test_a_new_row_carries_the_categorys_visit_time_and_the_osm_identity() -> None:
    from db.seed.finds import element_to_row

    row = element_to_row(_element(9, *GRODNO, military="bunker", name="ДОТ"), "укрепление")

    assert row is not None
    assert row["category"] == "укрепление"
    assert row["visit_minutes"] == "15"
    assert row["source_url"] == "osm:node/9"


def test_a_new_row_outside_the_project_area_is_dropped() -> None:
    from db.seed.finds import element_to_row

    assert (
        element_to_row(_element(1, *FAR_AWAY, military="bunker", name="ДОТ"), "укрепление") is None
    )


def test_the_committed_files_validate_as_the_new_dataset_and_sidecar() -> None:
    from db.seed.datasets import default_datasets, load_dataset, validate_record
    from db.seed.finds import read_place_category_entries

    finds = next(ds for ds in default_datasets() if ds.name == "finds")
    rows = load_dataset(finds)

    assert not any(validate_record(finds, row) for row in rows)
    assert {row["category"] for row in rows} <= {"укрепление", "валун"}
    entries = read_place_category_entries()
    assert entries, "the committed place_categories.json is empty"
    assert all(entry["category"] for entry in entries)


def test_the_stage_does_not_read_its_own_dataset() -> None:
    """A second run must not treat its last output as rows the datasets already hold.

    It did, and the result was a silently emptied `places_finds.csv`: 60 rows the next
    seed would have pruned from the database.
    """
    from db.seed.datasets import default_datasets, load_dataset
    from db.seed.finds import held_records

    held = held_records()
    finds = next(ds for ds in default_datasets() if ds.name == "finds")
    written = load_dataset(finds)

    assert written, "the committed dataset is empty — this test would pass vacuously"
    assert not [row for row in written if row["source_url"] in held]
    assert "osm:relation/1579104" in held or any(k.startswith("osm:") for k in held)

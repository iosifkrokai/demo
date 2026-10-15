"""What `seed fetch` must not lose when it rewrites a dataset.

The rows come from Overpass, which knows nothing about our districts: the column is
filled at fetch time, and a re-fetch that forgot to fill it would silently blank every
sight's district (the committed `places_osm_raw.csv` has one on every row). These tests
pin the offline fill and the name guard that stand between the source and the file.
"""

from __future__ import annotations

from typing import Any


def _row(source_url: str, lat: float, lon: float, **rest: Any) -> dict[str, Any]:
    return {"source_url": source_url, "lat": lat, "lon": lon, "district": "", **rest}


def test_a_refetch_keeps_the_district_only_where_no_boundary_answers() -> None:
    from db.seed.overpass import fill_districts

    rows = [_row("osm:node/1", 53.9000, 27.5667)]
    stats = fill_districts(rows, {"osm:node/1": "Слонимский район"})

    assert rows[0]["district"] == "Слонимский район"
    assert stats == {"carried": 1, "derived": 0}


def test_a_new_point_falls_back_to_the_nearest_raion_centre() -> None:
    from db.seed.overpass import fill_districts

    # Слоним — ближайший райцентр к этой точке; ответ обязан быть офлайновым.
    rows = [_row("osm:node/2", 53.0936, 25.3203)]
    stats = fill_districts(rows, {})

    assert rows[0]["district"] == "Слонимский район"
    assert stats == {"carried": 0, "derived": 1}


def test_a_district_already_present_is_left_alone() -> None:
    from db.seed.overpass import fill_districts

    # addr:district from the tags, or a row built by hand — never overwritten.
    rows = [_row("osm:node/3", 53.10, 25.30, district="Лидский район")]

    assert fill_districts(rows, {"osm:node/3": "Слонимский район"}) == {"carried": 0, "derived": 0}
    assert rows[0]["district"] == "Лидский район"


def test_the_guard_drops_an_excluded_name() -> None:
    from db.seed.cli import _drop_excluded_names

    kept, dropped = _drop_excluded_names(
        [{"name": "Заўжды цікаVа"}, {"name": "Кавярня на Савецкай"}]
    )

    assert dropped == 1
    assert [r["name"] for r in kept] == ["Кавярня на Савецкай"]


def test_a_church_tagged_as_an_attraction_is_still_a_church() -> None:
    """OSM marks notable churches `tourism=attraction`; the catch-all must not win.

    Reading the catch-all first put 419 churches, chapels and synagogues into
    `архитектура`, where «покажи костёлы» could not find them.
    """
    from db.seed.osm_tags import sight_tag_to_category

    assert (
        sight_tag_to_category(
            {
                "amenity": "place_of_worship",
                "religion": "christian",
                "denomination": "orthodox",
                "tourism": "attraction",
            }
        )
        == "церковь"
    )
    assert (
        sight_tag_to_category(
            {
                "amenity": "place_of_worship",
                "religion": "christian",
                "denomination": "catholic",
                "tourism": "attraction",
            }
        )
        == "костёл"
    )


def test_a_synagogue_and_a_chapel_read_as_a_temple() -> None:
    """«синагога» and «каплица» are the taxonomy's own aliases of `храм`."""
    from db.seed.osm_tags import sight_tag_to_category

    assert sight_tag_to_category({"amenity": "place_of_worship", "religion": "jewish"}) == "храм"
    assert sight_tag_to_category({"building": "chapel"}) == "храм"


def test_the_catch_all_tags_still_answer_when_nothing_sharper_does() -> None:
    from db.seed.osm_tags import sight_tag_to_category

    assert sight_tag_to_category({"tourism": "attraction"}) == "архитектура"
    assert sight_tag_to_category({"historic": "archaeological_site"}) == "архитектура"
    assert sight_tag_to_category({"tourism": "museum"}) == "музей"
    assert sight_tag_to_category({"historic": "castle"}) == "замок"
    assert sight_tag_to_category({"building": "castle"}) == "замок"
    assert sight_tag_to_category({"amenity": "cafe"}) is None


def test_a_manor_is_not_a_castle_and_a_palace_is_not_either() -> None:
    """`historic=manor` read as `замок` put 59 manor houses and palaces in the castles.

    The taxonomy gives `historic=manor` its own category (`усадьба`, whose `osm_tags`
    column has said `historic=manor|place=estate` all along) and `palace` another, so the
    code was contradicting the file that is the single source of category codes.
    """
    from db.seed.osm_tags import sight_tag_to_category

    assert sight_tag_to_category({"historic": "manor"}) == "усадьба"
    assert sight_tag_to_category({"historic": "palace"}) == "дворец"
    assert sight_tag_to_category({"building": "palace"}) == "дворец"
    assert sight_tag_to_category({"historic": "castle"}) == "замок"


def test_a_ruin_is_not_a_castle_but_a_castle_in_ruins_still_is() -> None:
    """`historic=ruins` is a condition, not a type: it decided `замок` for 222 rows."""
    from db.seed.osm_tags import sight_tag_to_category

    assert sight_tag_to_category({"historic": "ruins"}) == "архитектура"
    assert sight_tag_to_category({"historic": "castle", "ruins": "yes"}) == "замок"
    assert sight_tag_to_category({"building": "castle", "ruins": "yes"}) == "замок"


def test_a_stairway_belongs_to_the_steps_dataset_and_no_overpass_fetch() -> None:
    """`highway=steps` comes from the local extract, not from a sight/service fetch."""
    from db.seed.osm_tags import service_tag_to_category, sight_tag_to_category

    assert sight_tag_to_category({"highway": "steps"}) is None
    assert service_tag_to_category({"highway": "steps"}) is None

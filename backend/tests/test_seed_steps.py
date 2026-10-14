"""Stairways: the row built from an OSM way, the filter it passes, and its default."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

GRODNO = (53.6778, 23.8295)
FAR_AWAY = (49.0, 20.0)

HIGHLIGHTED_SERVICES = {"кафе", "ресторан", "туалет", "гостиница", "остановка транспорта"}


def _way(osm_id: int, lat: float, lon: float, **tags: str) -> dict[str, Any]:
    tags = {"highway": "steps", **tags}
    return {"type": "way", "id": osm_id, "center": {"lat": lat, "lon": lon}, "tags": tags}


def test_a_stairway_way_becomes_a_row_with_its_facts() -> None:
    from db.seed.steps import element_to_row

    row = element_to_row(_way(123, GRODNO[0], GRODNO[1], handrail="yes", step_count="12"))

    assert row is not None
    assert row["name"] == "Лестница"
    assert row["category"] == "лестница"
    assert row["source_url"] == "osm:way/123"
    assert row["lat"] == f"{GRODNO[0]:.6f}"
    assert row["lon"] == f"{GRODNO[1]:.6f}"
    assert row["visit_minutes"] == "1"
    assert row["blurb"] == ""
    assert row["fun_fact"] == ""
    facts = json.loads(str(row["fun_facts"]))
    assert "handrail=yes (OpenStreetMap)" in facts
    assert "step_count=12 (OpenStreetMap)" in facts


def test_the_name_osm_holds_is_kept_and_the_rest_carry_a_generated_label() -> None:
    """A name OSM states wins; where it states none, the label names the kind —
    the same generated label the stops and toilets carry, not an invented name."""
    from db.seed.osm_tags import is_fallback_name
    from db.seed.steps import element_to_row

    named = element_to_row(_way(7, GRODNO[0], GRODNO[1], name="Спуск к Неману"))
    unnamed = element_to_row(_way(8, GRODNO[0], GRODNO[1]))

    assert named is not None
    assert named["name"] == "Спуск к Неману"
    assert unnamed is not None
    assert is_fallback_name(str(unnamed["name"]))


def test_a_stairway_without_handrail_or_ramp_carries_no_fun_facts() -> None:
    from db.seed.steps import element_to_row

    row = element_to_row(_way(9, GRODNO[0], GRODNO[1]))

    assert row is not None
    assert row["fun_facts"] == "[]"


def test_a_way_that_is_not_a_stairway_is_not_a_row() -> None:
    from db.seed.steps import element_to_row

    bridge = {
        "type": "way",
        "id": 5,
        "center": {"lat": GRODNO[0], "lon": GRODNO[1]},
        "tags": {"highway": "footway"},
    }

    assert element_to_row(bridge) is None


def test_a_stairway_outside_the_project_area_is_dropped() -> None:
    from db.seed.steps import element_to_row

    assert element_to_row(_way(1, FAR_AWAY[0], FAR_AWAY[1])) is None
    assert element_to_row(_way(2, GRODNO[0], GRODNO[1])) is not None


def test_a_stairway_inside_the_oblast_ring_but_outside_the_fetch_box_is_dropped() -> None:
    """The datasets' own validators hold the fetch rectangle; a row past it would be a reject."""
    from db.seed.geofence import inside_project_area
    from db.seed.steps import element_to_row

    assert inside_project_area(54.857177, 26.322450)
    assert element_to_row(_way(1, 54.857177, 26.322450)) is None


def test_line_center_is_the_bounding_box_middle() -> None:
    from db.seed.steps import line_center

    assert line_center([(23.0, 53.0), (23.4, 53.2)]) == pytest.approx((53.1, 23.2))
    assert line_center([]) is None


def test_the_rows_are_written_in_the_14_column_pipe_format(tmp_path: Path) -> None:
    from db.seed.datasets import COLUMNS, read_pipe_csv
    from db.seed.steps import element_to_row, write_csv

    row = element_to_row(_way(29153929, GRODNO[0], GRODNO[1], handrail="yes"))
    assert row is not None
    path = tmp_path / "places_steps.csv"

    write_csv([row], path)
    lines = path.read_text(encoding="utf-8").splitlines()

    assert lines[0] == "# " + "|".join(COLUMNS)
    back = read_pipe_csv(path)
    assert back == [{column: str(row[column]) for column in COLUMNS}]


def test_the_committed_file_validates_as_the_steps_dataset() -> None:
    from db.seed.datasets import default_datasets, load_dataset, validate_record

    steps = next(ds for ds in default_datasets() if ds.name == "steps")
    rows = load_dataset(steps)

    assert rows, "the committed places_steps.csv is empty"
    assert all(row["category"] == "лестница" for row in rows)
    assert all(row["source_url"].startswith("osm:") for row in rows)
    assert not any(validate_record(steps, row) for row in rows)


def test_the_file_reader_marks_the_stairway_as_not_highlighted() -> None:
    """The taxonomy file, not the role, says what the map shows by default."""
    from db.seed.taxonomy import read_categories

    categories = {c.code: c for c in read_categories()}

    stairway = categories["лестница"]
    assert stairway.role == "service"
    assert stairway.highlight is False
    assert stairway.osm_tags == ("highway=steps",)
    assert stairway.visit_minutes == 1
    highlighted = {c.code for c in categories.values() if c.role == "service" and c.highlight}
    assert highlighted == HIGHLIGHTED_SERVICES


def test_the_service_default_still_names_the_same_categories(db_repos: Any) -> None:
    """Adding stairways must not move the map's default highlight (roadmap item 7)."""
    codes = db_repos.taxonomy.service_codes()

    assert set(codes) == HIGHLIGHTED_SERVICES
    assert "лестница" not in codes

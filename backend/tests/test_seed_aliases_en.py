"""The English-alias sidecar: which objects it names, and what it leaves alone."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

GRODNO = (53.6778, 23.8295)
FAR_AWAY = (49.0, 20.0)


def _record(source_url: str, name: str, *, lat: float, lon: float) -> dict[str, Any]:
    return {"source_url": source_url, "name": name, "lat": lat, "lon": lon}


def _targets(*records: dict[str, Any]) -> dict[str, dict[str, Any]]:
    from db.seed.aliases_en import osm_targets

    return osm_targets(records)


def test_an_osm_source_url_maps_to_the_object_behind_it() -> None:
    """``osm:node/123`` is the identity ``node/123``; the object we hold is the one named."""
    targets = _targets(
        _record("osm:node/123", "Мирский замок", lat=GRODNO[0], lon=GRODNO[1]),
        _record("osm_poi:way/456", "Кафе", lat=GRODNO[0], lon=GRODNO[1]),
    )

    assert set(targets) == {"node/123", "way/456"}
    assert targets["node/123"]["source_url"] == "osm:node/123"
    assert targets["way/456"]["name"] == "Кафе"


def test_a_record_that_is_not_an_osm_object_is_not_a_target() -> None:
    targets = _targets(
        _record("city:mir-castle", "Мирский замок", lat=GRODNO[0], lon=GRODNO[1]),
        _record("region:something", "Место", lat=GRODNO[0], lon=GRODNO[1]),
    )

    assert targets == {}


def test_a_record_outside_the_project_area_is_not_a_target() -> None:
    """The file may only name a place the project holds inside its region."""
    targets = _targets(
        _record("osm:node/1", "Чужое", lat=FAR_AWAY[0], lon=FAR_AWAY[1]),
    )

    assert targets == {}


def test_an_identity_two_records_claim_is_kept_once_by_reading_order() -> None:
    """A sight and a service mapped as one object must not flip the file between runs."""
    targets = _targets(
        _record("osm:node/9", "Первый", lat=GRODNO[0], lon=GRODNO[1]),
        _record("osm_poi:node/9", "Второй", lat=GRODNO[0], lon=GRODNO[1]),
    )

    assert list(targets) == ["node/9"]
    assert targets["node/9"]["source_url"] == "osm:node/9"


def test_an_object_we_do_not_hold_is_dropped() -> None:
    from db.seed.aliases_en import build_entries

    targets = _targets(_record("osm:node/123", "Мир", lat=GRODNO[0], lon=GRODNO[1]))

    entries, stats = build_entries(targets, {"node/123": "Mir Castle", "node/999": "Ghost"})

    assert [e["source_url"] for e in entries] == ["osm:node/123"]
    assert entries[0] == {
        "source_url": "osm:node/123",
        "locale": "en",
        "names": ["Mir Castle"],
        "source": "osm",
    }
    assert stats["matched"] == 1
    assert stats["written"] == 1


def test_the_first_candidate_of_a_semicolon_list_wins() -> None:
    from db.seed.aliases_en import alias_for

    assert alias_for("Mir Castle;Mirsky Castle", "Мирский замок") == "Mir Castle"
    assert alias_for("Mirsky Castle;Mir Castle", "Мирский замок") == "Mirsky Castle"


def test_a_name_that_repeats_the_row_is_not_an_alias() -> None:
    """A Cyrillic ``name:en`` equal to the row's own name carries nothing."""
    from db.seed.aliases_en import alias_for

    assert alias_for("Мирский замок", "Мирский замок") is None
    assert alias_for("  Мирский замок  ", "мирский   замок") is None


def test_a_different_cyrillic_value_is_kept() -> None:
    from db.seed.aliases_en import alias_for

    assert alias_for("Мірскі замак", "Мирский замок") == "Мірскі замак"


def test_entries_are_ordered_by_source_url_deterministically() -> None:
    from db.seed.aliases_en import build_entries

    targets = _targets(
        _record("osm:node/300", "Триста", lat=GRODNO[0], lon=GRODNO[1]),
        _record("osm:node/100", "Сто", lat=GRODNO[0], lon=GRODNO[1]),
    )
    found = {"node/300": "Three Hundred", "node/100": "One Hundred"}

    first, _ = build_entries(targets, found)
    second, _ = build_entries(
        dict(reversed(list(targets.items()))), dict(reversed(list(found.items())))
    )

    assert [e["source_url"] for e in first] == ["osm:node/100", "osm:node/300"]
    assert first == second


def test_the_written_document_reads_back_as_english_aliases(tmp_path: Path) -> None:
    from db.seed.aliases_en import build_entries, write_document
    from db.seed.datasets import read_place_alias_entries

    targets = _targets(_record("osm:node/123", "Мир", lat=GRODNO[0], lon=GRODNO[1]))
    entries, _ = build_entries(targets, {"node/123": "Mir Castle"})
    path = tmp_path / "place_aliases_en.json"

    write_document(path, entries)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert "description" in document
    assert read_place_alias_entries(path) == [
        {"source_url": "osm:node/123", "locale": "en", "alias": "Mir Castle"}
    ]


def test_an_alias_of_a_shadowed_row_resolves_through_the_survivors_source() -> None:
    """A row the seed shadowed is gone from ``places`` but its identity lives on in
    ``place_sources``; the English name must land on the survivor, not be dropped."""
    from db.seed.pipeline import resolve_alias_place

    by_source_url = {"region:mir-castle": 56}
    by_identity = {"openstreetmap/relation/1579104": 56}

    assert resolve_alias_place("region:mir-castle", by_source_url, by_identity) == 56
    assert resolve_alias_place("osm:relation/1579104", by_source_url, by_identity) == 56
    assert resolve_alias_place("osm:relation/9999999", by_source_url, by_identity) is None


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


def _place(conn: Any, source_url: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO places (name, category, category_source, lat, lon, source_url)
            VALUES ('Мир (тест)', 'замок', 'auto', %s, %s, %s)
            RETURNING id
            """,
            (GRODNO[0], GRODNO[1], source_url),
        )
        return int(cur.fetchone()[0])


def _alias_file(path: Path, source_url: str, alias: str) -> Path:
    path.write_text(
        json.dumps({"places": [{"source_url": source_url, "locale": "en", "names": [alias]}]}),
        encoding="utf-8",
    )
    return path


def test_applying_the_english_file_writes_en_rows_and_leaves_ru_alone(
    conn: Any, tmp_path: Path
) -> None:
    from db.seed import pipeline

    key = f"test:aliases-en/{uuid4().hex}"
    place_id = _place(conn, key)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO place_aliases (place_id, alias, locale, source) VALUES (%s, %s, 'ru', %s)",
            (place_id, "Мирский замок", "place_aliases.json"),
        )

    path = _alias_file(tmp_path / "place_aliases_en.json", key, "Mir Castle")
    stats = pipeline.apply_place_aliases(conn, path)

    assert stats["written"] == 1
    assert stats["unresolved"] == []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT alias, locale, source FROM place_aliases WHERE place_id = %s", (place_id,)
        )
        rows = {tuple(r) for r in cur.fetchall()}
    assert ("Mir Castle", "en", "place_aliases_en.json") in rows
    assert ("Мирский замок", "ru", "place_aliases.json") in rows


def test_applying_the_english_file_twice_leaves_one_row(conn: Any, tmp_path: Path) -> None:
    from db.seed import pipeline

    key = f"test:aliases-en/{uuid4().hex}"
    place_id = _place(conn, key)
    path = _alias_file(tmp_path / "place_aliases_en.json", key, "Mir Castle")

    pipeline.apply_place_aliases(conn, path)
    stats = pipeline.apply_place_aliases(conn, path)

    assert stats["written"] == 1
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM place_aliases WHERE place_id = %s AND locale = 'en'", (place_id,)
        )
        assert cur.fetchone()[0] == 1


def test_an_alias_keyed_on_a_shadowed_row_lands_on_its_survivor(conn: Any, tmp_path: Path) -> None:
    """The row is gone from ``places`` but its ``place_sources`` identity moved to the
    survivor, so the English name is attached to the place the visitor belongs in."""
    from db.seed import pipeline

    shadowed_url = f"osm:relation/{uuid4().int}"
    survivor = _place(conn, f"test:aliases-en/survivor-{uuid4().hex}")
    src = pipeline.source_fields(shadowed_url)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO place_sources (place_id, provider, external_id, url) "
            "VALUES (%s, %s, %s, %s)",
            (survivor, src["provider"], src["external_id"], src["url"]),
        )

    path = _alias_file(tmp_path / "place_aliases_en.json", shadowed_url, "Mir Castle")
    stats = pipeline.apply_place_aliases(conn, path)

    assert stats["written"] == 1
    assert stats["unresolved"] == []
    with conn.cursor() as cur:
        cur.execute("SELECT alias, locale FROM place_aliases WHERE place_id = %s", (survivor,))
        assert cur.fetchall() == [("Mir Castle", "en")]

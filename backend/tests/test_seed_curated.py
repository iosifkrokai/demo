"""Which row the curated overlay lands on, and what it does with a name that has several."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from core.paths import DATA_DIR, PLACES_DIR
from db.seed.datasets import collapse, collect_records, default_datasets, match_place, read_curated

PREFIX = "test:curated/"
HAND: dict[str, str] = {"category_source": "dataset"}
AUTO: dict[str, str] = {"category_source": "auto"}

CURATED_CSV = PLACES_DIR / "places_curated.csv"
RETIRED_CSV = DATA_DIR / "curated_retired.csv"


def _row(place_id: int, name: str, lat: float, lon: float, kind: dict[str, str]) -> dict[str, Any]:
    return {"id": place_id, "name": name, "lat": lat, "lon": lon, "category": "музей", **kind}


def test_a_hand_authored_row_is_the_one_the_curated_text_was_written_for() -> None:
    """The overlay reaches its target by name alone, so the twin 1.2 km away must not
    win the run: the hand-authored row is the place, the OSM node is a copy of it."""
    from db.seed.datasets import match_place

    hand = _row(13, "Дом-музей Элизы Ожешко", 53.6779, 23.8242, HAND)
    twin = _row(205, "Дом-музей Элизы Ожешко", 53.684171, 23.839514, AUTO)

    assert match_place("Дом-музей Элизы Ожешко", [twin, hand]) == (hand, "exact")


def test_the_lower_id_wins_between_equal_rows() -> None:
    """No heap order: two automatic rows of one name in one place answer by id."""
    from db.seed.datasets import match_place

    first = _row(1006, "Еврейское кладбище", 53.289685, 24.396779, AUTO)
    second = _row(1782, "Еврейское кладбище", 53.28970, 24.39680, AUTO)

    assert match_place("Еврейское кладбище", [second, first]) == (first, "exact")


def test_a_name_shared_by_rows_kilometres_apart_is_ambiguous() -> None:
    """Three cemeteries 70 to 190 km apart under one name are three places: the overlay
    says so instead of putting one city's text on another's grave."""
    from db.seed.datasets import match_place

    rows = [
        _row(1006, "Еврейское кладбище", 54.404574, 26.607576, AUTO),
        _row(1782, "Еврейское кладбище", 53.456225, 25.406486, AUTO),
        _row(2424, "Еврейское кладбище", 53.289685, 24.396779, AUTO),
    ]

    assert match_place("Еврейское кладбище", rows) == (None, "ambiguous")


def test_an_absent_name_is_not_ambiguous() -> None:
    from db.seed.datasets import match_place

    rows = [_row(1, "Фарный костёл", 53.6778, 23.8295, AUTO)]

    assert match_place("Дом офицеров", rows) == (None, "none")


def _projected_rows() -> list[dict[str, Any]]:
    """The rows the seed projects, as ``match_place`` reads them.

    Built from the committed datasets alone, so a curated row resolves without a database."""
    collected = collect_records(default_datasets())
    kept, _shadowed, _stale = collapse(collected["records"])
    return [
        {
            "id": index,
            "name": r["name"],
            "category": r["category"],
            "category_source": r["_category_source"],
            "lat": r["lat"],
            "lon": r["lon"],
        }
        for index, r in enumerate(kept)
    ]


def test_every_row_of_the_read_curated_file_still_resolves() -> None:
    """The file the seed reads holds only the rows that reach a place; the prose written
    for a vintage the data no longer carries was moved to ``curated_retired.csv``. A row
    left here that resolves to nothing is a silent loss, not a retirement."""
    rows = _projected_rows()
    unresolved = sorted(
        r["name"] for r in read_curated(CURATED_CSV) if match_place(r["name"], rows)[0] is None
    )
    assert not unresolved, f"a curated row in the read file resolves to nothing: {unresolved}"


def test_the_retired_file_holds_only_rows_that_still_reach_nothing() -> None:
    """The archive is the complement: a row in it that resolves again belongs back in the
    read file, where the overlay would reach it again."""
    rows = _projected_rows()
    again = sorted(
        r["name"] for r in read_curated(RETIRED_CSV) if match_place(r["name"], rows)[0] is not None
    )
    assert not again, f"an archived row resolves again — it belongs in the read file: {again}"


def test_the_split_accounts_for_the_whole_file() -> None:
    """The rows only moved: the read file and the archive are disjoint and add up to the
    file as it was."""
    kept = {r["id"] for r in read_curated(CURATED_CSV)}
    retired = {r["id"] for r in read_curated(RETIRED_CSV)}
    assert kept
    assert retired
    assert not (kept & retired)
    assert len(kept) == 17
    assert len(retired) == 59
    assert len(kept) + len(retired) == 76


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


def _place(conn: Any, source_url: str, name: str, *, source: str, lat: float, lon: float) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO places (name, category, category_source, lat, lon, source_url)
            VALUES (%s, 'музей', %s, %s, %s, %s)
            RETURNING id
            """,
            (name, source, lat, lon, source_url),
        )
        return int(cur.fetchone()[0])


def _source_of(conn: Any, place_id: int) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT category_source FROM places WHERE id = %s", (place_id,))
        return str(cur.fetchone()[0])


def _curated(name: str, blurb: str = "Кураторский текст.") -> dict[str, Any]:
    return {
        "id": 1,
        "name": name,
        "category": "музей",
        "blurb": blurb,
        "fun_fact": None,
        "fun_facts": None,
        "links": None,
    }


def test_the_overlay_marks_the_hand_authored_row_and_leaves_the_twin_alone(conn: Any) -> None:
    from db.seed import pipeline

    name = "Дом-музей Элизы Ожешко (тест)"
    hand = _place(conn, "test:curated/hand", name, source="dataset", lat=53.6779, lon=23.8242)
    twin = _place(conn, "test:curated/twin", name, source="auto", lat=53.684171, lon=23.839514)

    stats = pipeline.apply_curated_rows(conn, [_curated(name)])

    assert stats["matched"] == 1
    assert _source_of(conn, hand) == "curated"
    assert _source_of(conn, twin) == "auto"


def test_an_ambiguous_name_is_reported_and_marks_nothing(conn: Any) -> None:
    from db.seed import pipeline

    name = "Еврейское кладбище (тест)"
    far = _place(conn, "test:curated/far", name, source="auto", lat=54.404574, lon=26.607576)
    near = _place(conn, "test:curated/near", name, source="auto", lat=53.289685, lon=24.396779)

    stats = pipeline.apply_curated_rows(conn, [_curated(name)])

    assert stats["ambiguous"] == [name]
    assert stats["ambiguous_count"] == 1
    assert stats["unmatched"] == []
    assert (_source_of(conn, far), _source_of(conn, near)) == ("auto", "auto")


def test_resetting_curated_gives_the_row_back_to_its_dataset(conn: Any) -> None:
    """The flag is protection, so one left behind by an earlier run is a row no writer
    can ever correct — the seed rebuilds it from the datasets it is holding."""
    from db.seed import pipeline

    stale = _place(
        conn, f"{PREFIX}stale", "Памятник (тест)", source="curated", lat=53.6778, lon=23.8295
    )
    records = [{"source_url": f"{PREFIX}stale", "_category_source": "dataset"}]

    counts = pipeline.reset_curated(conn, records)

    assert counts["reset"] >= 1
    assert _source_of(conn, stale) == "dataset"


def test_resetting_leaves_a_row_no_dataset_accounts_for(conn: Any) -> None:
    from db.seed import pipeline

    orphan = _place(
        conn, f"{PREFIX}orphan", "Памятник (тест)", source="curated", lat=53.6778, lon=23.8295
    )

    counts = pipeline.reset_curated(conn, [])

    assert counts["reset"] == 0
    assert counts["unknown"] >= 1
    assert _source_of(conn, orphan) == "curated"


def test_an_apply_lands_on_the_same_row_twice(conn: Any) -> None:
    """Two applies over the same data leave one curated row, not two: the report of three
    names carrying both twins came from exactly this."""
    from db.seed import pipeline

    name = "Дом-музей Элизы Ожешко (тест)"
    hand = _place(conn, "test:curated/hand2", name, source="dataset", lat=53.6779, lon=23.8242)
    _place(conn, "test:curated/twin2", name, source="auto", lat=53.684171, lon=23.839514)

    first = pipeline.apply_curated_rows(conn, [_curated(name)])
    second = pipeline.apply_curated_rows(conn, [_curated(name)])

    assert first["matched"] == second["matched"] == 1
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM places WHERE name = %s AND category_source = 'curated'", (name,)
        )
        assert [r[0] for r in cur.fetchall()] == [hand]

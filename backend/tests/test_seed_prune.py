"""What ``prune_stale`` deletes, and what it must leave alone."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

KEPT = "test:prune/kept"
STALE = "test:prune/stale"
PREFIX = "test:prune/"


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


def _place(conn: Any, source_url: str, *, category_source: str = "auto") -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO places (name, category, category_source, lat, lon, source_url)
            VALUES ('Объект для теста', 'памятник', %s, 53.6777, 23.8300, %s)
            RETURNING id
            """,
            (category_source, source_url),
        )
        return int(cur.fetchone()[0])


def _visit(conn: Any, place_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, email, password_hash) "
            "VALUES ('00000000-0000-0000-0000-0000000000ff', 'prune@test.invalid', 'x') "
            "ON CONFLICT (id) DO NOTHING"
        )
        cur.execute(
            "INSERT INTO visited_places (user_id, place_id) VALUES "
            "('00000000-0000-0000-0000-0000000000ff', %s)",
            (place_id,),
        )


def _itinerary_stop(conn: Any, source_url: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO itineraries (id, title) VALUES ('test-prune', 'Тестовый маршрут') "
            "ON CONFLICT (id) DO NOTHING"
        )
        cur.execute(
            "INSERT INTO itinerary_stops (itinerary_id, ordinal, source_url) "
            "VALUES ('test-prune', 0, %s)",
            (source_url,),
        )


def _survivors(conn: Any) -> set[str]:
    """The test's own rows left in the table, whatever is in the caller's list."""
    with conn.cursor() as cur:
        cur.execute("SELECT source_url FROM places WHERE source_url LIKE %s", (f"{PREFIX}%",))
        return {r[0] for r in cur.fetchall()}


def test_only_the_rows_no_dataset_carries_are_deleted(conn: Any) -> None:
    """One pass: what leaves, and everything that must not."""
    from db.seed.pipeline import prune_stale

    _place(conn, KEPT)
    _place(conn, STALE)
    _place(conn, f"{PREFIX}dataset", category_source="dataset")
    _place(conn, f"{PREFIX}curated", category_source="curated")
    _visit(conn, _place(conn, f"{PREFIX}visited"))
    _place(conn, f"{PREFIX}itinerary")
    _itinerary_stop(conn, f"{PREFIX}itinerary")

    deleted = prune_stale(conn, [KEPT], apply=True)

    assert deleted >= 1
    assert _survivors(conn) == {
        KEPT,
        f"{PREFIX}dataset",
        f"{PREFIX}curated",
        f"{PREFIX}visited",
        f"{PREFIX}itinerary",
    }


def test_counting_alone_deletes_nothing(conn: Any) -> None:
    """The caller decides, the query only says."""
    from db.seed.pipeline import prune_stale

    _place(conn, STALE)

    found = prune_stale(conn, [], apply=False)

    assert found >= 1
    assert _survivors(conn) == {STALE}


def test_a_row_a_dataset_still_carries_is_left_alone(conn: Any) -> None:
    """Nothing to prune means nothing happens."""
    from db.seed.pipeline import prune_stale

    _place(conn, KEPT)

    prune_stale(conn, [KEPT, f"{PREFIX}also-current"], apply=True)

    assert _survivors(conn) == {KEPT}


def test_a_dataset_nobody_could_read_blocks_the_sweep():
    """An absent file is not evidence that its rows are gone."""
    from db.seed.cli import _prune_stale

    collected = {"datasets": [{"name": "osm", "missing": True}], "records": []}

    note = _prune_stale(None, collected, skip=False)

    assert note == "skipped (datasets absent: osm)"


def test_no_prune_is_a_note_and_nothing_else():
    from db.seed.cli import _prune_stale

    assert _prune_stale(None, {"datasets": [], "records": []}, skip=True) == (
        "skipped (--no-prune)"
    )

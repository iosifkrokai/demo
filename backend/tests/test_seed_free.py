"""Whether admission is free: the seed writes the fact, the card carries it, and no
dash is left in the data to read as one."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from core.paths import PLACES_DIR


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


def _dataset() -> Any:
    from db.seed.datasets import Dataset

    return Dataset(
        name="test",
        filename="none.csv",
        prefix="test:",
        reader=lambda path: [],
        validator=lambda row: [],
        normalizer=lambda row: row,
        category_source="auto",
        fatal_invalid=False,
        license="test",
    )


def _record(source_url: str, ticket_price: str | None) -> dict[str, Any]:
    return {
        "name": f"Место {source_url}",
        "category": "музей",
        "district": None,
        "town": None,
        "lat": 53.6778,
        "lon": 23.8295,
        "blurb": None,
        "fun_fact": None,
        "fun_facts": None,
        "opening_hours": None,
        "ticket_price": ticket_price,
        "visit_minutes": 20,
        "links": None,
        "source_url": source_url,
        "_category_source": "auto",
    }


def _is_free_of(conn: Any, source_url: str) -> Any:
    with conn.cursor() as cur:
        cur.execute("SELECT is_free FROM places WHERE source_url = %s", (source_url,))
        found = cur.fetchall()
    return found[0][0] if found else "absent"


def test_apply_dataset_writes_the_three_readings_and_survives_a_null(conn: Any) -> None:
    from db.seed import pipeline

    records = [
        _record("test:free/no", "no"),
        _record("test:free/price", "≈4 BYN"),
        _record("test:free/silent", None),
        _record("test:free/dash", "—"),
    ]

    stats = pipeline.apply_dataset(conn, _dataset(), records)

    assert stats["inserted"] == 4
    assert _is_free_of(conn, "test:free/no") is True
    assert _is_free_of(conn, "test:free/price") is False
    assert _is_free_of(conn, "test:free/silent") is None
    assert _is_free_of(conn, "test:free/dash") is None


def test_upsert_sql_declares_the_column_and_its_binding() -> None:
    from db.seed.pipeline import upsert_sql

    statement = upsert_sql()

    assert "is_free" in statement
    assert "%(is_free)s" in statement
    assert "is_free = EXCLUDED.is_free" in statement


def test_no_dash_is_left_in_the_ticket_price_column() -> None:
    from db.seed.datasets import read_pipe_csv

    for filename in ("places_grodno_city.csv", "places_region.csv"):
        rows = read_pipe_csv(PLACES_DIR / filename)
        assert rows, filename
        for row in rows:
            assert (row["ticket_price"] or "").strip() not in ("—", "–", "-")


def test_the_card_projection_carries_the_free_fact() -> None:
    from agent.tools.places import card_of
    from db.store.mappers import place_from_row, place_payload

    free = place_from_row({"id": 1, "name": "Место", "is_free": True})
    paid = place_from_row({"id": 2, "name": "Место", "is_free": False})
    unknown = place_from_row({"id": 3, "name": "Место", "is_free": None})

    assert card_of(free)["is_free"] is True
    assert card_of(paid)["is_free"] is False
    assert card_of(unknown)["is_free"] is None
    assert place_payload(free)["is_free"] is True

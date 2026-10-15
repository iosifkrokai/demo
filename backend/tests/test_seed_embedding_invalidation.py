"""A renamed row must lose the vector that spells its old name.

`embed_missing` only ever fills `embedding IS NULL`, so a writer that changes the text a
vector was built from has to clear it in the same statement — otherwise the row keeps
matching a name it no longer has, and no later seed ever repairs it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest


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


def _record(source_url: str, name: str, blurb: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "category": "музей",
        "district": None,
        "town": None,
        "lat": 53.6778,
        "lon": 23.8295,
        "blurb": blurb,
        "fun_fact": None,
        "fun_facts": None,
        "opening_hours": None,
        "ticket_price": None,
        "visit_minutes": 20,
        "links": None,
        "source_url": source_url,
        "_category_source": "auto",
    }


def _put_vector(conn: Any, source_url: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE places SET embedding = array_fill(0.5, ARRAY[384])::vector "
            "WHERE source_url = %s",
            (source_url,),
        )


def _has_vector(conn: Any, source_url: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT embedding IS NOT NULL FROM places WHERE source_url = %s", (source_url,))
        found = cur.fetchall()
    return bool(found and found[0][0])


def test_the_upsert_drops_the_vector_when_the_name_changes(conn: Any) -> None:
    from db.seed import pipeline

    pipeline.apply_dataset(conn, _dataset(), [_record("test:embed/rename", "Старое имя")])
    _put_vector(conn, "test:embed/rename")

    pipeline.apply_dataset(conn, _dataset(), [_record("test:embed/rename", "Новое имя")])

    assert _has_vector(conn, "test:embed/rename") is False


def test_the_upsert_keeps_the_vector_when_the_text_is_the_same(conn: Any) -> None:
    from db.seed import pipeline

    pipeline.apply_dataset(conn, _dataset(), [_record("test:embed/same", "Имя", "Описание")])
    _put_vector(conn, "test:embed/same")

    pipeline.apply_dataset(conn, _dataset(), [_record("test:embed/same", "Имя", "Описание")])

    assert _has_vector(conn, "test:embed/same") is True


def test_the_upsert_drops_the_vector_when_the_blurb_changes(conn: Any) -> None:
    from db.seed import pipeline

    pipeline.apply_dataset(conn, _dataset(), [_record("test:embed/blurb", "Имя", "Старое")])
    _put_vector(conn, "test:embed/blurb")

    pipeline.apply_dataset(conn, _dataset(), [_record("test:embed/blurb", "Имя", "Новое")])

    assert _has_vector(conn, "test:embed/blurb") is False


def test_the_streets_overlay_drops_the_vector_it_renames(conn: Any, tmp_path: Any) -> None:
    from db.seed import pipeline

    pipeline.apply_dataset(conn, _dataset(), [_record("test:embed/street", "Остановка")])
    _put_vector(conn, "test:embed/street")
    sidecar = tmp_path / "place_streets.json"
    sidecar.write_text(
        '{"places": [{"source_url": "test:embed/street", "name": "Остановка",'
        ' "street": "улица Тестовая", "dist_m": 5.0, "source": "osm_street"}]}',
        encoding="utf-8",
    )

    pipeline.apply_place_streets(conn, sidecar)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, embedding IS NOT NULL FROM places WHERE source_url = %s",
            ("test:embed/street",),
        )
        found = cur.fetchall()
    assert found
    assert found[0][0] == "Остановка (улица Тестовая)"
    assert found[0][1] is False


def test_the_embedded_text_is_the_name_and_the_blurb() -> None:
    from db.seed.embeddings import embedded_text

    assert embedded_text("Мирский замок", None) == "Мирский замок. "
    assert embedded_text("Мирский замок", "Замок в Мире") == "Мирский замок. Замок в Мире"

    from db.seed.pipeline import stale_embedding

    fragment = stale_embedding("EXCLUDED.name", "EXCLUDED.blurb")
    assert "places.name IS DISTINCT FROM EXCLUDED.name" in fragment
    assert "places.blurb IS DISTINCT FROM EXCLUDED.blurb" in fragment

"""What ``load_areas`` writes onto ``areas.geom``: the region keeps its ring, the rest stay bare."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import pytest

from core.paths import GEO_DIR

GEOMETRY_LESS = ("grodno-city", "grodno-old-town", "lida-district", "oshmyany-district")


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


def _bbox(conn: Any, code: str) -> tuple[float, float, float, float] | None:
    """(south, west, north, east) as the repository reads it, or None without a polygon."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ST_YMin(ext), ST_XMin(ext), ST_YMax(ext), ST_XMax(ext) "
            "  FROM (SELECT ST_Extent(geom::geometry) AS ext FROM areas WHERE code = %s) AS boxed",
            (code,),
        )
        row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return (float(row[0]), float(row[1]), float(row[2]), float(row[3]))


def _file_bounds() -> tuple[float, float, float, float]:
    """The rectangle the border file itself spans, (south, west, north, east)."""
    rings = json.loads((GEO_DIR / "grodno_border.json").read_text(encoding="utf-8"))["rings"][0]
    lons = [p[0] for p in rings]
    lats = [p[1] for p in rings]
    return (min(lats), min(lons), max(lats), max(lons))


def _force_ring(conn: Any, code: str) -> None:
    """Give a row a wrong ring first, so clearing it is what a re-seed must do."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE areas SET geom = ST_GeogFromText("
            "'MULTIPOLYGON(((20 50, 21 50, 21 51, 20 51, 20 50)))') WHERE code = %s",
            (code,),
        )


def test_the_region_keeps_the_file_ring(conn: Any) -> None:
    from db.seed.pipeline import load_areas

    load_areas(conn, GEO_DIR)

    assert _bbox(conn, "grodno-oblast") == pytest.approx(_file_bounds())


def test_an_area_without_a_polygon_gets_none(conn: Any) -> None:
    from db.seed.pipeline import load_areas

    _force_ring(conn, "grodno-old-town")
    assert _bbox(conn, "grodno-old-town") is not None

    load_areas(conn, GEO_DIR)

    assert _bbox(conn, "grodno-old-town") is None


def test_only_the_polygon_less_areas_stay_bare(conn: Any) -> None:
    from db.seed.pipeline import load_areas

    _force_ring(conn, "grodno-old-town")
    for code in GEOMETRY_LESS:
        _force_ring(conn, code)

    load_areas(conn, GEO_DIR)

    for code in GEOMETRY_LESS:
        assert _bbox(conn, code) is None, code


def test_a_polygon_less_area_is_still_a_registry_row(conn: Any) -> None:
    from db.seed.pipeline import load_areas

    load_areas(conn, GEO_DIR)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT name_ru, kind, is_project_area FROM areas WHERE code = 'grodno-old-town'"
        )
        row = cur.fetchone()
        cur.execute("SELECT aliases FROM areas WHERE code = 'grodno-old-town'")
        (aliases,) = cur.fetchone()

    assert row == ("Старый город", "city-quarter", False)
    assert "Старый город" in aliases


def _legacy(conn: Any, code: str, name: str) -> None:
    """A row an earlier seed vintage wrote, which no file declares any more."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO areas (code, name_ru, kind, is_project_area, geom) "
            "VALUES (%s, %s, 'project-area', false, "
            "        ST_GeogFromText('MULTIPOLYGON(((20 50, 21 50, 21 51, 20 51, 20 50)))'))",
            (code, name),
        )


def test_an_area_no_file_declares_is_deleted_with_its_ring(conn: Any) -> None:
    """«Гродненская область» resolves to the code the file holds, not to a row of the
    same name that an older seed left with its own ring — the file is the registry."""
    from db.seed.pipeline import load_areas

    _legacy(conn, "test:legacy-voblast", "Гродненская область")

    landed = load_areas(conn, GEO_DIR)

    assert landed["removed"] >= 1
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM areas WHERE code = 'test:legacy-voblast'")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM areas WHERE code = 'grodno-oblast'")
        assert cur.fetchone()[0] == 1


def test_the_table_holds_exactly_the_areas_the_file_declares(conn: Any) -> None:
    from db.seed.pipeline import load_areas

    declared = json.loads((GEO_DIR / "areas.json").read_text(encoding="utf-8"))["areas"]

    landed = load_areas(conn, GEO_DIR)

    assert landed["written"] == len(declared)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM areas")
        assert cur.fetchone()[0] == len(declared)

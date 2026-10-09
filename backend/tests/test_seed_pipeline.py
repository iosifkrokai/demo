"""Offline tests for the reproducible seed pipeline (spec 002, W4).

No network and no database: the DB connect helper and the network stack are
monkeypatched to fail loudly, and every fixture is a small CSV written to a
pytest ``tmp_path``.
"""

from __future__ import annotations

import json
import re
import socket
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from domain import constants  # noqa: E402
from seed import (  # noqa: E402
    cli as seed_cli,
    datasets,
    pipeline,
)

MIGRATION = BACKEND / "db" / "migrations" / "0004_places_taxonomy.sql"

HEADER = ("# name|category|district|town|lat|lon|blurb|fun_fact|fun_facts|"
          "opening_hours|ticket_price|visit_minutes|links|source_url")

# A Grodno-region point that the geofence accepts, and Vilnius (inside the
# generous ingest bbox, outside the project area).
IN_AREA = (53.6791, 23.8216)
OUT_OF_AREA = (54.6872, 25.2797)


def _row(name, category, source_url, lat, lon, *, hours="", price="",
         visit="90", district="Гродно (город)", town="Гродно",
         fun_facts="[]", links="[]"):
    return "|".join([
        name, category, district, town, f"{lat}", f"{lon}", "blurb", "fact",
        fun_facts, hours, price, visit, links, source_url,
    ])


def _write_datasets(data_dir: Path, *, city_rows, region_rows, osm_rows,
                    curated_rows=()):
    (data_dir / "places_grodno_city.csv").write_text(
        "\n".join([HEADER, *city_rows]) + "\n", encoding="utf-8")
    (data_dir / "places_region.csv").write_text(
        "\n".join([HEADER, *region_rows]) + "\n", encoding="utf-8")
    (data_dir / "places_osm_raw.csv").write_text(
        "\n".join([HEADER, *osm_rows]) + "\n", encoding="utf-8")
    if curated_rows:
        (data_dir / "places_curated.csv").write_text(
            "\n".join([
                "# id|normalized_name|category|blurb|fun_fact|fun_facts|links",
                *curated_rows,
            ]) + "\n",
            encoding="utf-8")


@pytest.fixture()
def fixture_dir(tmp_path: Path) -> Path:
    """A tiny, valid three-dataset fixture with one geofence reject."""
    _write_datasets(
        tmp_path,
        city_rows=[
            _row("Старый замок (Гродно)", "замок", "city:old-castle", *IN_AREA,
                 hours="вт-вс 10:00-18:00", price="7 BYN"),
            _row("Коложская церковь", "церковь", "city:kolozha",
                 IN_AREA[0] + 0.0005, IN_AREA[1] + 0.0005),
        ],
        region_rows=[
            _row("Мирский замок", "замок", "region:mir-castle", 53.4513, 26.4729,
                 district="Кореличский район", hours="круглосуточно", price="16 BYN"),
        ],
        osm_rows=[
            _row("Лидский замок", "замок", "osm:way/1", 53.8845, 25.2925,
                 district="Лидский район", town="Лида"),
            # Same name, ~2 m away → suspected duplicate of the row above.
            _row("Лидский замок", "замок", "osm:way/2", 53.88451, 25.29251,
                 district="Лидский район", town="Лида"),
            # Inside the bbox, outside Grodno voblast → quarantined.
            _row("Cafe Vilnius", "музей", "osm:node/3", *OUT_OF_AREA,
                 district="", town=""),
        ],
        curated_rows=[
            "1|Старый замок|замок|curated blurb|curated fact|[]|[]",
            "2|Коложская церковь|церковь|curated blurb|curated fact|[]|[]",
        ],
    )
    return tmp_path


# --dry-run must never touch the DB or the network

def test_dry_run_needs_no_db_and_no_network(fixture_dir, tmp_path, monkeypatch):
    def _explode(*_args, **_kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("--dry-run touched the database")

    monkeypatch.setattr(pipeline, "connect", _explode)
    monkeypatch.setattr(socket, "create_connection", _explode)

    report_path = tmp_path / "report.json"
    rc = seed_cli.main(["--dry-run", "--data-dir", str(fixture_dir),
                        "--report", str(report_path)])

    assert rc == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "dry-run"
    assert "db" not in report  # no apply happened


def test_dry_run_report_is_stable_across_runs(fixture_dir):
    def _report():
        datasets_ = [d.with_data_dir(fixture_dir) for d in datasets.default_datasets()]
        return datasets.build_coverage_report(
            datasets.collect_records(datasets_), mode="dry-run", generated_at="fixed")

    first, second = _report(), _report()
    assert first == second  # no counters drift between runs


# Validation + quarantine

def test_collect_records_tags_category_source(fixture_dir):
    datasets_ = [d.with_data_dir(fixture_dir) for d in datasets.default_datasets()]
    collected = datasets.collect_records(datasets_)
    sources = {r["_dataset"]: r["_category_source"] for r in collected["records"]}
    assert sources["city"] == datasets.SOURCE_DATASET
    assert sources["region"] == datasets.SOURCE_DATASET
    assert sources["osm"] == datasets.SOURCE_AUTO


def test_geofence_rejects_are_quarantined_and_not_fatal(fixture_dir):
    datasets_ = [d.with_data_dir(fixture_dir) for d in datasets.default_datasets()]
    collected = datasets.collect_records(datasets_)
    assert collected["fatal"] is False
    rejected = [r for r in collected["rejects"] if r["dataset"] == "osm"]
    assert len(rejected) == 1
    assert rejected[0]["kind"] == "geofence"
    assert "outside Grodno region" in rejected[0]["problems"][0]
    # The rejected row is not among the published records.
    assert all(r["source_url"] != "osm:node/3" for r in collected["records"])


def test_bad_category_in_hand_authored_dataset_is_fatal(tmp_path):
    _write_datasets(
        tmp_path,
        city_rows=[_row("Выдуманное место", "выдумка", "city:fake", *IN_AREA)],
        region_rows=[],
        osm_rows=[],
    )
    rc = seed_cli.main(["--dry-run", "--data-dir", str(tmp_path)])
    assert rc == 2  # region/city must never be half-loaded


def test_non_finite_coordinates_are_invalid_not_geofence(tmp_path):
    _write_datasets(
        tmp_path,
        city_rows=[],
        region_rows=[],
        osm_rows=[_row("Странная точка", "музей", "osm:node/9", "nan", "nan")],
    )
    datasets_ = [d.with_data_dir(tmp_path) for d in datasets.default_datasets()]
    collected = datasets.collect_records(datasets_)
    assert collected["fatal"] is False
    kinds = {r["kind"] for r in collected["rejects"]}
    assert kinds == {"invalid"}


# Coverage report — the numbers must be honest and exact

def test_coverage_counts_and_shares(fixture_dir):
    datasets_ = [d.with_data_dir(fixture_dir) for d in datasets.default_datasets()]
    collected = datasets.collect_records(datasets_)
    curated = datasets.read_curated(fixture_dir / "places_curated.csv")
    report = datasets.build_coverage_report(
        collected, mode="dry-run", curated=curated,
        curated_stats={"rows": len(curated), "applied": False})

    # 5 valid records (2 city, 1 region, 2 osm); the Vilnius row is quarantined.
    assert report["totals"]["records"] == 5
    assert report["totals"]["geofence_rejects"] == 1
    assert report["totals"]["invalid"] == 0

    cov = report["coverage"]
    assert cov["source_url"] == {"count": 5, "total": 5, "share": 1.0}
    assert cov["coordinates"]["share"] == 1.0
    # 2 of 5 fixture rows carry opening_hours (city row 1 + region row).
    assert cov["opening_hours"] == {"count": 2, "total": 5, "share": 0.4}
    assert cov["ticket_price"] == {"count": 2, "total": 5, "share": 0.4}

    assert report["by_category"]["замок"] == 4
    assert report["by_category"]["церковь"] == 1
    assert report["by_district"]["Лидский район"] == 2
    assert report["geofence_rejects"]["by_dataset"] == {"osm": 1}


def test_duplicate_detection_flags_close_same_name(fixture_dir):
    datasets_ = [d.with_data_dir(fixture_dir) for d in datasets.default_datasets()]
    collected = datasets.collect_records(datasets_)
    dups = datasets.find_suspected_duplicates(collected["records"])
    assert len(dups) == 1
    pair = dups[0]
    assert pair["name"] == "лидский замок"
    assert pair["distance_m"] < datasets.DEFAULT_DUP_RADIUS_M
    assert {pair["a"]["source_url"], pair["b"]["source_url"]} == {"osm:way/1", "osm:way/2"}


def test_alias_coverage_counts_ru_names(fixture_dir):
    datasets_ = [d.with_data_dir(fixture_dir) for d in datasets.default_datasets()]
    collected = datasets.collect_records(datasets_)
    report = datasets.build_coverage_report(collected, mode="dry-run")
    aliases = report["aliases"]
    assert aliases["ru_script_names"]["count"] == 5
    assert aliases["en_script_names"]["count"] == 0
    assert aliases["explicit_en_aliases"]["count"] == 0  # unknown stays unknown


def test_report_json_roundtrip(fixture_dir, tmp_path):
    report_path = tmp_path / "r.json"
    assert seed_cli.main(["--dry-run", "--data-dir", str(fixture_dir),
                          "--report", str(report_path)]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for key in ("totals", "by_category", "by_district", "coverage", "aliases",
                "geofence_rejects", "invalid_rows", "suspected_duplicates", "curated"):
        assert key in report, key


# Curated-category protection

@pytest.mark.parametrize("source,expected", [
    ("curated", True), ("dataset", True), ("auto", False), (None, False),
])
def test_curated_category_is_protected(source, expected):
    assert pipeline.curated_category_is_protected(source) is expected


def test_upsert_sql_never_overwrites_protected_categories():
    sql = pipeline.upsert_sql()
    assert "ON CONFLICT (source_url) DO UPDATE" in sql
    assert "CASE WHEN places.category_source IN ('curated', 'dataset')" in sql
    # Protected rows keep their category against automatic writers only.
    assert "AND EXCLUDED.category_source = 'auto'" in sql
    assert sql.count("THEN places.category ELSE EXCLUDED.category END") == 1
    assert sql.count("THEN places.category_source ELSE EXCLUDED.category_source END") == 1


def test_source_fields_maps_providers():
    assert pipeline.source_fields("osm:way/1")["provider"] == "openstreetmap"
    assert pipeline.source_fields("osm:way/1")["external_id"] == "way/1"
    assert pipeline.source_fields("city:old-castle")["provider"] == "planetabelarus"
    assert pipeline.source_fields("city:old-castle")["url"] is None


# Migration 0004

def test_migration_adds_all_three_tables_and_is_idempotent():
    sql = MIGRATION.read_text(encoding="utf-8")
    for table in ("place_aliases", "place_sources", "areas"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, table
    # No bare CREATE TABLE / INDEX anywhere (would break a re-run).
    assert not re.search(r"CREATE TABLE(?! IF NOT EXISTS)", sql)
    assert not re.search(r"CREATE (?:UNIQUE )?INDEX(?! IF NOT EXISTS)", sql)
    assert "ADD COLUMN IF NOT EXISTS category_source" in sql
    assert "DROP TRIGGER IF EXISTS places_guard_curated_category" in sql
    assert "CREATE OR REPLACE FUNCTION places_guard_curated_category" in sql
    # Indexes the retrieval paths rely on.
    assert "USING GIN (alias gin_trgm_ops)" in sql
    assert "USING GIST (geom)" in sql
    assert "place_sources (provider, external_id)" in sql


def test_migration_guard_reverts_protected_category_changes():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "OLD.category_source IN ('curated', 'dataset')" in sql
    assert "grodno.allow_curated_category_change" in sql
    assert "NEW.category := OLD.category" in sql


# Real repo data stays consistent with the seed contract

def test_real_curated_csv_is_parseable_and_in_taxonomy():
    curated = datasets.read_curated(BACKEND / "data" / "places_curated.csv")
    assert len(curated) == 76
    for row in curated:
        assert row["category"] in constants.CATEGORIES, row["name"]
        assert row["name"]

"""Schema and provenance checks for the route-benchmark reference set.

The benchmark is only as honest as its reference walks: a case with no source, or with a
coordinate that does not exist in our DB, silently turns every quality number into noise. These
tests pin the shape, the provenance fields, the region and the DB cross-check.

Run: backend/.venv/bin/python -m pytest -q tests/test_bench_routes_data.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROUTES_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "routes"

# The three walks that predate the benchmark_meta convention. They are grandfathered
# deliberately: anything new must carry provenance.
LEGACY = {"grodno_old_town.json", "mir.json", "novogrudok.json"}

REGION = {"south": 52.75, "west": 23.35, "north": 54.80, "east": 27.00}
GRADES = {"must-see", "nice-to-have", "available"}
META_KEYS = {"license", "retrieved_at", "coords_source"}


def cases() -> list[tuple[str, dict]]:
    files = sorted(ROUTES_DIR.glob("*.json"))
    assert files, f"no reference walks in {ROUTES_DIR}"
    return [(f.name, json.loads(f.read_text(encoding="utf-8"))) for f in files]


def graded_cases() -> list[tuple[str, dict]]:
    return [(name, case) for name, case in cases() if name not in LEGACY]


def test_legacy_set_is_frozen():
    """A new case without benchmark_meta must not sneak in through the legacy door."""
    names = {name for name, _ in cases()}
    assert names >= LEGACY, "the original three walks disappeared"
    extra = names - LEGACY - {n for n, c in cases() if "benchmark_meta" in c}
    assert not extra, f"cases without provenance: {sorted(extra)}"


def test_every_case_has_a_query_and_stops():
    for name, case in cases():
        assert case.get("name"), f"{name}: no name"
        assert case.get("query_ru"), f"{name}: no query_ru"
        assert isinstance(case.get("budget_minutes"), int), f"{name}: budget_minutes"
        assert "stops" in case, f"{name}: no stops"
        if name not in LEGACY and case.get("case_meta", {}).get("kind") != "adversarial":
            assert case["stops"], f"{name}: no stops at all"


@pytest.mark.parametrize("name,case", graded_cases(), ids=lambda v: v if isinstance(v, str) else "")
def test_graded_cases_carry_provenance(name: str, case: dict):
    meta = case.get("benchmark_meta") or {}
    assert set(meta) >= META_KEYS, f"{name}: benchmark_meta needs {META_KEYS}"
    assert meta["license"], f"{name}: empty license"
    assert meta["retrieved_at"], f"{name}: empty retrieved_at"
    assert case.get("case_meta", {}).get("kind"), f"{name}: case_meta.kind missing"


@pytest.mark.parametrize("name,case", graded_cases(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_stop_is_located_graded_and_sourced(name: str, case: dict):
    for stop in case["stops"]:
        label = f"{name} :: {stop.get('name')}"
        lat, lon = stop.get("lat"), stop.get("lon")
        assert isinstance(lat, (int, float)) and isinstance(lon, (int, float)), f"{label}: coordinates"
        assert REGION["south"] <= lat <= REGION["north"], f"{label}: lat {lat} outside the region"
        assert REGION["west"] <= lon <= REGION["east"], f"{label}: lon {lon} outside the region"
        assert stop.get("grade") in GRADES, f"{label}: grade {stop.get('grade')!r}"
        urls = stop.get("source_urls") or []
        assert urls, f"{label}: no source_urls"
        assert all(u.startswith("http") for u in urls), f"{label}: bad source url"
        if stop["grade"] == "must-see":
            assert len(set(urls)) >= 2, f"{label}: grade 3 needs >= 2 independent sources"


def test_place_ids_exist_in_the_database_when_it_is_reachable():
    """Cross-check stop -> DB row. Skips cleanly when there is no database (CI, offline)."""
    ids = {
        stop["place_id"]
        for _, case in graded_cases()
        for stop in case["stops"]
        if stop.get("place_id") is not None
    }
    if not ids:
        pytest.skip("no place_id recorded")

    try:
        import psycopg  # noqa: PLC0415 - optional dependency of this check

        from agent.config import settings  # noqa: PLC0415 - lazy: no DB import graph at collection

        with psycopg.connect(settings.DSN, connect_timeout=3) as conn:
            found = {
                row[0]
                for row in conn.execute(
                    "SELECT id FROM places WHERE id = ANY(%s)", (sorted(ids),)
                ).fetchall()
            }
    except Exception as exc:
        pytest.skip(f"database not reachable: {type(exc).__name__}")

    missing = sorted(ids - found)
    assert not missing, f"reference stops with a place_id that is not in the DB: {missing}"


def test_coordinates_agree_with_the_database_rows():
    """A stop with a place_id must sit on that row (within 250 m), unless the case says otherwise."""
    checked = skipped = 0
    try:
        import psycopg  # noqa: PLC0415 - optional dependency of this check

        from agent.config import settings  # noqa: PLC0415 - lazy: no DB import graph at collection

        conn = psycopg.connect(settings.DSN, connect_timeout=3)
    except Exception as exc:
        pytest.skip(f"database not reachable: {type(exc).__name__}")

    with conn:
        for name, case in graded_cases():
            case_note = case.get("coords_note") or ""
            for stop in case["stops"]:
                pid = stop.get("place_id")
                # A stop whose coordinate deliberately comes from the OSM object instead of the
                # (wrong) curated DB row is exempt — the case documents it.
                stop_note = stop.get("note") or ""
                if "OSM-объект" in stop_note or (case_note and stop["name"] in case_note):
                    skipped += 1
                    continue
                if pid is None:
                    skipped += 1
                    continue
                row = conn.execute("SELECT lat, lon FROM places WHERE id = %s", (pid,)).fetchone()
                if row is None:
                    continue
                d_lat = abs(float(row[0]) - float(stop["lat"])) * 111_320
                d_lon = (
                    abs(float(row[1]) - float(stop["lon"]))
                    * 111_320
                    * abs(__import__("math").cos(float(stop["lat"]) * 3.14159265 / 180))
                )
                assert (d_lat**2 + d_lon**2) ** 0.5 <= 250, (
                    f"{name} :: {stop['name']} is not on its DB row (id={pid})"
                )
                checked += 1
    assert checked, "nothing was cross-checked - is every stop relocated?"

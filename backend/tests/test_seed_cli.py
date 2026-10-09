"""The one seed command: offline by default, one guarded upsert, versioned fetch.

No DB and no network in this file.
"""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

from seed import cli, datasets, pipeline

BACKEND = Path(__file__).resolve().parents[1]
DATA = BACKEND / "data"


def _copy_datasets(dst: Path) -> None:
    for name in ("places_grodno_city.csv", "places_region.csv", "places_osm_raw.csv"):
        shutil.copy(DATA / name, dst / name)


def _no_db(*_a, **_kw):
    raise AssertionError("this path must not touch the database")


def _no_net(*_a, **_kw):
    raise AssertionError("this path must not touch the network")


def test_dry_run_is_offline_and_exits_0(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "connect", _no_db)
    monkeypatch.setattr(socket, "create_connection", _no_net)

    report = tmp_path / "seed.json"
    rc = cli.main(["--dry-run", "--report", str(report)])

    assert rc == 0
    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["mode"] == "dry-run"
    assert "db" not in written            # nothing was written, nothing to report
    assert written["totals"]["records"] > 0


def test_invalid_hand_authored_row_is_fatal(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _copy_datasets(data_dir)

    # Corrupt one city row: an out-of-region coordinate in a hand-authored set
    # must never half-load.
    city = data_dir / "places_grodno_city.csv"
    lines = city.read_text(encoding="utf-8").splitlines()
    body = [ln for ln in lines if ln.strip() and not ln.startswith("#")]
    cells = body[0].split("|")
    cells[4] = "10.0"  # lat, far outside Grodno
    body[0] = "|".join(cells)
    city.write_text("\n".join(body) + "\n", encoding="utf-8")

    assert cli.main(["--dry-run", "--data-dir", str(data_dir)]) == 2


def test_one_guarded_upsert_for_every_dataset():
    sql = pipeline.upsert_sql()
    assert "ON CONFLICT (source_url) DO UPDATE" in sql
    # The curated-category guard: an automatic writer can never clobber curation.
    assert "places.category_source IN ('curated', 'dataset')" in sql
    assert "EXCLUDED.category_source = 'auto'" in sql
    # No dataset carries a second, unguarded INSERT of its own any more.
    for ds in datasets.default_datasets():
        assert not hasattr(ds, "upsert_sql")


def test_fetch_from_input_json_writes_a_versioned_csv(tmp_path):
    overpass_response = {
        "elements": [
            {"type": "node", "id": 2, "lat": 53.6772, "lon": 23.8232,
             "tags": {"amenity": "cafe", "name": "Кафе Тест"}},
            # Outside the project area — must be dropped, not written.
            {"type": "node", "id": 3, "lat": 54.6872, "lon": 25.2797,
             "tags": {"amenity": "cafe", "name": "Vilnius Cafe"}},
        ]
    }
    src = tmp_path / "overpass.json"
    src.write_text(json.dumps(overpass_response), encoding="utf-8")

    out_dir = tmp_path / "out"
    rc = cli.main(["fetch", "--source", "poi", "--input-json", str(src),
                   "--out-dir", str(out_dir)])

    assert rc == 0
    csv = out_dir / "places_poi.csv"
    assert csv.exists()
    text = csv.read_text(encoding="utf-8")
    assert "osm_poi:node/2" in text
    assert "Vilnius" not in text

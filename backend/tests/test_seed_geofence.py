"""Offline regression tests for every path that inserts places into Postgres."""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import ingest_osm  # noqa: E402
import ingest_poi  # noqa: E402
import load_osm  # noqa: E402
import parse_places  # noqa: E402
import seed_region  # noqa: E402

from agent.geofence import inside_project_area  # noqa: E402

# Both locations are in Belarus and inside the generous ingest bbox, but
# belong to Brest/Minsk voblasts rather than Grodno.
OTHER_VOBLASTS = [("Барановичи", 53.1307, 26.0139), ("Вилейка", 54.4903, 26.9107)]


def test_project_geofence_excludes_other_belarus_voblasts():
    for name, lat, lon in OTHER_VOBLASTS:
        assert not inside_project_area(lat, lon), name
    assert inside_project_area(53.6772, 23.8232)


def test_osm_ingest_paths_exclude_other_voblasts():
    for _, lat, lon in OTHER_VOBLASTS:
        element = {"type": "node", "id": 7, "lat": lat, "lon": lon,
                   "tags": {"name": "Музей", "tourism": "museum"}}
        assert ingest_osm.osm_element_to_row(element) is None
        element["tags"] = {"name": "Кафе", "amenity": "cafe"}
        assert ingest_poi.osm_element_to_row(element) is None


def test_csv_loaders_reject_other_voblasts_and_nonfinite_coordinates():
    city = seed_region.normalize(seed_region.read_rows(BACKEND / "data" / "places_grodno_city.csv")[0])
    osm = load_osm.read_csv(BACKEND / "data" / "places_osm_raw.csv")[0]
    for _, lat, lon in OTHER_VOBLASTS:
        assert seed_region.validate(dict(city, lat=lat, lon=lon))
        assert load_osm.validate(dict(osm, lat=str(lat), lon=str(lon)))
    assert seed_region.validate(dict(city, lat=float("nan")))
    assert load_osm.validate(dict(osm, lat="nan"))


def test_scraper_rejects_other_voblasts():
    html = '<h1 class="publications">Музей</h1><script>const initialCenter = [26.0139, 53.1307];</script>'
    assert parse_places.parse_detail("https://planetabelarus.by/sights/museum/", html) is None


def test_curated_seed_records_validate_clean():
    for path, _prefix in seed_region.DATASETS:
        for raw in seed_region.read_rows(path):
            row = seed_region.normalize(raw)
            problems = seed_region.validate(row)
            assert not problems, f"{path.name}: {row['name']}: {'; '.join(problems)}"

"""Offline regression tests for every path that inserts places into Postgres."""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from domain.geofence import inside_project_area  # noqa: E402
from seed.datasets import (  # noqa: E402
    read_pipe_csv,
    validate_city_region,
    validate_osm_sight,
)
from seed.osm_tags import service_element_to_row, sight_element_to_row  # noqa: E402

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
        assert sight_element_to_row(element) is None
        element["tags"] = {"name": "Кафе", "amenity": "cafe"}
        assert service_element_to_row(element) is None


def test_csv_loaders_reject_other_voblasts_and_nonfinite_coordinates():
    # The validators take the raw pipe rows the way collect_records() feeds them
    # (normalize_* is the separate, typed pass that runs after validation).
    city = read_pipe_csv(BACKEND / "data" / "places_grodno_city.csv")[0]
    osm = read_pipe_csv(BACKEND / "data" / "places_osm_raw.csv")[0]
    for _, lat, lon in OTHER_VOBLASTS:
        assert validate_city_region(dict(city, lat=lat, lon=lon))
        assert validate_osm_sight(dict(osm, lat=str(lat), lon=str(lon)))
    assert validate_city_region(dict(city, lat=float("nan")))
    assert validate_osm_sight(dict(osm, lat="nan"))


def test_curated_seed_records_validate_clean():
    for name in ("places_grodno_city.csv", "places_region.csv"):
        path = BACKEND / "data" / name
        for raw in read_pipe_csv(path):
            problems = validate_city_region(raw)
            assert not problems, f"{path.name}: {raw['name']}: {'; '.join(problems)}"

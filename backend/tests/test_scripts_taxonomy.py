"""One taxonomy, checked at the seams the seed scripts used to duplicate it.

`data/taxonomy.csv` (read through `agent/taxonomy.py`) is the single source of
category codes and their visit times. Three seed scripts carried their own copy
of that knowledge; the copies were already drifting (the validation allow-lists
excluded the service codes, so a café row would have been dropped silently).
These tests pin the single source in place:

  * seed_region / load_osm validation allow-lists = the taxonomy's sight role;
  * ingest_osm visit times = the taxonomy's visit_minutes;
  * ingest_poi's OSM tag → code map stays explicit (a bare `amenity=place_of_worship`
    is genuinely ambiguous between костёл/церковь/монастырь/храм, so it cannot be
    derived) but must AGREE with the taxonomy's own `osm_tags`;
  * the district centres cover every district, spelled as the data spells it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# scripts/ is a directory of directly-runnable scripts, not a package: ingest_poi
# imports ingest_osm flat, so the flat form is how they are loaded here too
# (same convention as tests/test_ingest_poi.py).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import load_osm
import seed_region
from ingest_osm import RAION_CENTRES, visit_minutes_for
from ingest_poi import (
    AMENITY_CATEGORY,
    HIGHWAY_CATEGORY,
    PUBLIC_TRANSPORT_CATEGORY,
    RAILWAY_CATEGORY,
    TOURISM_CATEGORY,
    extract_name,
    tag_to_category,
)

from agent import services, taxonomy

DATA = Path(__file__).resolve().parents[1] / "data"


def _sight_codes() -> frozenset[str]:
    return frozenset(cat.code for cat in taxonomy.all_categories() if cat.role == "sight")


# ── the validation allow-lists ──────────────────────────────────────────────

def test_seed_region_allow_list_is_the_taxonomy_sight_role():
    assert set(seed_region.TAXONOMY) == _sight_codes()
    assert set(load_osm.TAXONOMY) == _sight_codes()


def test_the_region_csvs_only_use_categories_the_taxonomy_knows():
    """A row the allow-list would reject is a data defect, not an import surprise."""
    seen: set[str] = set()
    for name in ("places_grodno_city.csv", "places_region.csv"):
        for line in (DATA / name).read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            seen.add(line.split("|")[1])
    assert seen <= _sight_codes(), f"unknown categories in the CSVs: {seen - _sight_codes()}"


# ── visit times ─────────────────────────────────────────────────────────────

def test_ingest_osm_visit_times_come_from_the_taxonomy():
    for cat in taxonomy.all_categories():
        assert visit_minutes_for(cat.code) == cat.visit_minutes


def test_ingest_osm_visit_time_falls_back_for_unknown_category():
    assert visit_minutes_for("нет такой категории") == 20


# ── the OSM tag maps ────────────────────────────────────────────────────────

def test_ingest_poi_tag_map_codes_exist_in_the_taxonomy():
    for code in (*AMENITY_CATEGORY.values(), *TOURISM_CATEGORY.values()):
        assert taxonomy.get(code) is not None, f"{code!r} is not a taxonomy code"


def test_ingest_poi_tag_map_agrees_with_the_taxonomy_osm_tags():
    """The map may not claim a tag the taxonomy does not list for that code."""
    for osm_key, mapping in (("amenity", AMENITY_CATEGORY), ("tourism", TOURISM_CATEGORY)):
        for tag_value, code in mapping.items():
            tags = taxonomy.get(code).osm_tags
            assert f"{osm_key}={tag_value}" in tags, (
                f"{osm_key}={tag_value} → {code!r}, but {code!r} lists {tags}"
            )


# ── public-transport stops are services, and the ingest knows all three spellings ──


def test_transit_tag_maps_agree_with_the_taxonomy_osm_tags():
    """Same rule as the amenity/tourism maps: a map may not claim an unlisted tag.

    Three OSM spellings mean one thing to a tourist at the kerb — a stop you can
    board — so all three must land on the same code, and that code must list each
    of them.
    """
    for osm_key, mapping in (
        ("highway", HIGHWAY_CATEGORY),
        ("public_transport", PUBLIC_TRANSPORT_CATEGORY),
        ("railway", RAILWAY_CATEGORY),
    ):
        for tag_value, code in mapping.items():
            assert code == "остановка", f"{osm_key}={tag_value} → {code!r}"
            assert f"{osm_key}={tag_value}" in taxonomy.get(code).osm_tags, (
                f"{osm_key}={tag_value} → {code!r}, but {code!r} lists "
                f"{taxonomy.get(code).osm_tags}"
            )
            assert tag_to_category({osm_key: tag_value}) == code


def test_a_transit_stop_is_a_service_not_a_sight():
    """It is never «visited» — it is where the walk can be cut short.

    The visit time is the taxonomy's smallest positive default (the file's own
    contract is `visit_minutes > 0`), not 0: a boarding point is not a place to
    spend time, and if a request ever turns it into a stop it must not cost zero.
    """
    stop = taxonomy.get("остановка")
    assert stop.role == "service"
    assert taxonomy.visit_minutes("остановка") > 0
    assert taxonomy.visit_minutes("остановка") <= 5
    assert "остановка" in services.service_codes(), (
        "the services-along-the-route lookup reads the taxonomy, so a service code "
        "must be reachable from it or the boarding points never appear"
    )


def test_an_unnamed_transit_stop_survives_with_a_placeholder_name():
    """Most OSM bus stops carry no name; dropping them would hide the boarding
    point the tourist asked for."""
    assert extract_name({"highway": "bus_stop", "addr:street": "ул. Советская"}, "остановка") == (
        "Остановка (ул. Советская)"
    )
    assert extract_name({"highway": "bus_stop"}, "остановка") == "Остановка"
    assert extract_name({"highway": "bus_stop", "name": "Вокзал"}, "остановка") == "Вокзал"


# ── districts ───────────────────────────────────────────────────────────────

def test_every_district_has_a_centre_and_slonim_is_not_missing():
    areas = json.loads((DATA / "areas.json").read_text(encoding="utf-8"))["areas"]
    districts = [
        a for a in areas if a["kind"] == "district" and a["slug"] != "grodno-district"
    ]
    missing = [
        a["name_ru"] for a in districts if not any(name in a["name_ru"] for name in RAION_CENTRES)
    ]
    assert not missing, f"districts with no centre: {missing}"
    assert "Слонимский" in RAION_CENTRES


def test_volkovysk_is_spelled_with_two_s():
    """The dataset's typo («Волковыссккий») must not come back, and the old
    spelling stays resolvable as a legacy alias."""
    areas = json.loads((DATA / "areas.json").read_text(encoding="utf-8"))["areas"]
    volkovysk = next(a for a in areas if a["slug"] == "volkovysk-district")

    assert volkovysk["name_ru"] == "Волковысский район"
    assert "Волковыссккий район" in volkovysk["aliases"]["ru"], "legacy spelling must still resolve"

    for line in (DATA / "places_region.csv").read_text(encoding="utf-8").splitlines():
        if "Волковыск" in line:
            assert "Волковыссккий" not in line

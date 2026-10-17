"""The district column, measured against the committed boundaries rather than guessed."""

from __future__ import annotations

from typing import Any

LYUBCHA = (53.7521858, 26.0692601)
KOLOZHA = (53.6808, 23.8305)
KORELICHI = (53.5642, 26.1400)
MINSK = (53.9000, 27.5667)


def test_a_town_closer_to_the_neighbouring_centre_keeps_its_own_district() -> None:
    from db.seed.districts import district_at

    assert district_at(*LYUBCHA) == "Новогрудский район"


def test_the_city_wins_over_the_district_that_surrounds_it() -> None:
    from db.seed.districts import district_at

    assert district_at(*KOLOZHA) == "Гродно (город)"


def test_a_district_centre_stands_in_its_own_district() -> None:
    from db.seed.districts import district_at

    assert district_at(*KORELICHI) == "Кореличский район"


def test_a_point_outside_every_boundary_answers_nothing() -> None:
    from db.seed.districts import district_at

    assert district_at(*MINSK) is None


def test_every_area_that_had_a_shape_withheld_now_has_one() -> None:
    import json
    from pathlib import Path

    from db.seed.districts import districts

    path = Path(__file__).resolve().parents[1] / "db" / "data" / "geo" / "areas.json"
    areas = json.loads(path.read_text(encoding="utf-8"))["areas"]
    named = {area["name_ru"] for area in areas if area["kind"] in ("district", "city")}
    drawn = {str(district["name_ru"]) for district in districts()}
    in_district_column = {"Гродно": "Гродно (город)"}
    assert drawn == {in_district_column.get(name, name) for name in named}


def test_the_boundary_file_says_where_it_came_from() -> None:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "db" / "data" / "geo" / "districts_border.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "OpenStreetMap" in payload["source"]
    assert payload["license"]
    assert payload["attribution"]
    assert payload["fetched_on"]
    assert payload["tolerance_deg"] <= 0.0005


def test_a_point_in_the_hairline_between_two_rings_takes_the_nearest() -> None:
    from db.seed.districts import district_at

    assert district_at(54.538228, 26.623251) == "Сморгонский район"


def test_the_committed_districts_are_the_ones_the_boundaries_give() -> None:
    """The column is derived, not authored: a row disagreeing with its polygon is a bug.

    Every dataset carrying the column is walked, so a new one cannot arrive with guesses."""
    import csv
    import io
    from pathlib import Path

    from db.seed.districts import district_at

    directory = Path(__file__).resolve().parents[1] / "db" / "data" / "places"
    checked = 0
    for path in sorted(directory.glob("places_*.csv")):
        text = path.read_text(encoding="utf-8")
        header, _, body = text.partition("\n")
        columns = next(csv.reader([header.lstrip("# ")], delimiter="|"))
        if "district" not in columns:
            continue
        index = columns.index("district")
        for row in csv.reader(io.StringIO(body), delimiter="|"):
            if len(row) <= index or not row[index]:
                continue
            try:
                lat, lon = float(row[4]), float(row[5])
            except ValueError:
                continue
            beside = district_at(lat, lon)
            if beside is None:
                continue
            checked += 1
            where = f"{path.name}: {row[0]} помечен {row[index]}, а лежит в {beside}"
            assert row[index] == beside, where
    assert checked > 8000


def test_a_refetch_takes_the_boundary_over_a_carried_district() -> None:
    from db.seed.overpass import fill_districts

    row: dict[str, Any] = {"source_url": "osm:way/243087446", "lat": LYUBCHA[0], "lon": LYUBCHA[1]}
    stats = fill_districts([row], {"osm:way/243087446": "Кореличский район"})

    assert row["district"] == "Новогрудский район"
    assert stats == {"carried": 0, "derived": 1}


def test_a_point_outside_the_boundaries_still_keeps_its_snapshot() -> None:
    from db.seed.overpass import fill_districts

    row: dict[str, Any] = {"source_url": "osm:node/1", "lat": MINSK[0], "lon": MINSK[1]}
    stats = fill_districts([row], {"osm:node/1": "Слонимский район"})

    assert row["district"] == "Слонимский район"
    assert stats == {"carried": 1, "derived": 0}


def test_a_point_outside_the_boundaries_falls_back_to_the_nearest_centre() -> None:
    from db.seed.overpass import fill_districts

    row: dict[str, Any] = {"source_url": "osm:node/2", "lat": MINSK[0], "lon": MINSK[1]}
    stats = fill_districts([row], {})

    assert row["district"].endswith(" район")
    assert stats == {"carried": 0, "derived": 1}

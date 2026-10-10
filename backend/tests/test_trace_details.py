"""The traces that answer «почему этой остановки нет в маршруте»."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import constants
from planner.models import Candidate, LatLon
from planner.pipeline import (
    _drop_duplicates,
    _dupe_pairs,
    _geo_focus,
    _geo_focus_report,
)


def _c(pid: int, name: str, lat: float, lon: float, rrf_score: float = 0.0) -> Candidate:
    return Candidate(
        id=pid,
        name=name,
        category="замок",
        lat=lat,
        lon=lon,
        rrf_score=rrf_score,
    )


def test_the_report_returns_exactly_what_the_focus_returned():
    """The trace may not change the answer, only explain it."""
    pool = [
        _c(1, "Anchor", lat=53.95, lon=26.47, rrf_score=1.0),
        _c(2, "Near", lat=53.96, lon=26.48, rrf_score=0.9),
        _c(3, "Far", lat=54.1, lon=27.0, rrf_score=0.8),
    ]
    assert [c.id for c in _geo_focus(pool, anchor_id=1)] == [
        c.id for c in _geo_focus_report(pool, anchor_id=1)[0]
    ]
    assert _geo_focus_report([], anchor_id=1)[0] == []


def test_a_dropped_place_reports_its_own_distance_to_the_anchor():
    """«Убрано 1» becomes a judgement the reader can check: 78 km, radius 12."""
    anchor = _c(1, "Мирский замок", lat=53.9506, lon=26.4675, rrf_score=1.0)
    near = _c(2, "Костёл Николая (Мир)", lat=53.948, lon=26.470, rrf_score=0.8)
    far = _c(3, "Старый замок Гродно", lat=53.6693, lon=23.8301, rrf_score=0.6)

    keep, report = _geo_focus_report([anchor, near, far], anchor_id=1)

    assert {c.id for c in keep} == {1, 2}
    assert report["anchor"] == "Мирский замок"
    assert report["radius_km"] == constants.GEO_FOCUS_KM
    gone, = report["dropped"]
    assert gone["name"] == "Старый замок Гродно"
    assert gone["km"] > 100


def test_the_radius_reported_is_the_one_actually_used():
    """The discovery branch doubles; a reader must not compare against the constant."""
    anchor_c = _c(1, "Anchor", lat=53.95, lon=26.47, rrf_score=1.0)
    near = _c(2, "Near", lat=53.95, lon=26.50, rrf_score=0.9)
    far = _c(3, "Far", lat=53.95, lon=26.75, rrf_score=0.8)

    keep, report = _geo_focus_report([anchor_c, near, far])

    assert {c.id for c in keep} == {1, 2, 3}
    assert report["radius_km"] == constants.GEO_FOCUS_KM * 2
    assert report["dropped"] == []


def test_the_discovery_radius_stops_at_its_ceiling():
    """A pool that never reaches three stops keeps the widest radius, not a loop."""
    a = _c(1, "Alone", lat=53.95, lon=26.47, rrf_score=1.0)
    b = _c(2, "Also", lat=55.60, lon=26.47, rrf_score=0.9)

    keep, report = _geo_focus_report([a, b])

    assert {c.id for c in keep} == {1}
    assert report["radius_km"] == constants.GEO_FOCUS_DISCOVERY_MAX_KM
    assert report["dropped"][0]["name"] == "Also"


def test_nothing_to_report_when_nothing_was_dropped():
    pool = [_c(1, "A", lat=53.95, lon=26.47), _c(2, "B", lat=53.96, lon=26.48)]
    _, report = _geo_focus_report(pool, anchor_id=1)
    assert report["dropped"] == []
    assert report["anchor"] == "A"


def test_a_gps_anchor_is_named_as_such():
    """`origin` is the tourist's own position; the trace must not call it a place."""
    origin_c = _c(1, "Whatever", lat=53.95, lon=26.47)
    near = _c(2, "Near", lat=53.96, lon=26.48)
    _, report = _geo_focus_report(
        [origin_c, near], origin=LatLon(lat=53.95, lon=26.47)
    )
    assert report["anchor"] == "GPS"


def test_a_duplicate_names_the_place_it_was_folded_into():
    """The same castle under two names, 0 m apart: the reader sees the fold."""
    curated = _c(1, "Новый замок (дворец Стефана Батория)", lat=53.6770, lon=23.8250)
    osm = _c(2, "Новый замок", lat=53.6770, lon=23.8250)

    after = _drop_duplicates([curated, osm], constants.DUPLICATE_RADIUS_M)
    pairs = _dupe_pairs([curated, osm], after, constants.DUPLICATE_RADIUS_M)

    assert [c.id for c in after] == [1]
    assert pairs == [
        {"dropped": "Новый замок", "into": "Новый замок (дворец Стефана Батория)", "m": 0}
    ]


def test_the_pairing_carries_the_distance_that_justified_it():
    """340 m apart, same normalised name — that is the whole reason it went."""
    a = _c(1, "Дом-музей Адама Мицкевича", lat=53.6770, lon=23.8250)
    b = _c(2, "Дом-музей Адама Мицкевича", lat=53.6800, lon=23.8250)

    after = _drop_duplicates([a, b], constants.DUPLICATE_RADIUS_M)
    pairs = _dupe_pairs([a, b], after, constants.DUPLICATE_RADIUS_M)

    assert len(pairs) == 1
    assert 300 < pairs[0]["m"] < 400


def test_two_distinct_places_produce_no_pairing():
    a = _c(1, "Старый замок", lat=53.6770, lon=23.8250)
    b = _c(2, "Фарный костёл", lat=53.6790, lon=23.8290)

    after = _drop_duplicates([a, b], constants.DUPLICATE_RADIUS_M)
    assert [c.id for c in after] == [1, 2]
    assert _dupe_pairs([a, b], after, constants.DUPLICATE_RADIUS_M) == []


def test_the_pairing_answers_for_every_row_the_step_removed():
    """The count and the list must reconcile — a silent drop would not."""
    pool = [
        _c(1, "Старый замок", lat=53.6770, lon=23.8250),
        _c(2, "Старый замок", lat=53.6771, lon=23.8251),
        _c(3, "Фарный костёл", lat=53.6790, lon=23.8290),
        _c(4, "Фарный костёл (дубль)", lat=53.6790, lon=23.8291),
    ]
    after = _drop_duplicates(pool, constants.DUPLICATE_RADIUS_M)
    pairs = _dupe_pairs(pool, after, constants.DUPLICATE_RADIUS_M)

    assert len(pool) - len(after) == len(pairs) == 2

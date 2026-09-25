"""Phase 0 of the two-mode UI: the refinement contract (RouteContext).

The frontend sends the route it already has plus the delta instruction; the
backend must accept it, ignore it safely on a first turn, and never resurrect a
stop the user deleted by hand.
"""

from __future__ import annotations

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import constants
from agent.models import Candidate, GenerateReq, LatLon, RouteContext
from agent.planner.pipeline import (
    _cap_for_valhalla,
    _context_changes,
    _drop_excluded,
    _nearby_convenience,
    _with_base_points,
)


def _cand(pid: int, name: str) -> Candidate:
    return Candidate(
        id=pid, name=name, lat=52.1, lon=23.7, category="кафе",
        score=1.0, distance_m=None, source="osm",
    )


def test_request_without_context_still_parses():
    req = GenerateReq(query="костёлы Гродно", origin=LatLon(lat=53.6, lon=23.8))
    assert req.context is None


def test_context_round_trips_from_the_frontend_payload():
    req = GenerateReq.model_validate({
        "query": "музеи Гродно",
        "context": {
            "instruction": "добавь кофейню и туалет",
            "revision": 2,
            "excluded_ids": [17],
            "base_points": [
                {"id": 3, "name": "Новый замок", "lat": 53.6766, "lon": 23.8264},
                {"id": None, "name": "моё местоположение", "lat": 53.66,
                 "lon": 23.83, "source": "mine", "pinned": True},
            ],
        },
    })
    ctx = req.context
    assert ctx is not None
    assert ctx.instruction == "добавь кофейню и туалет"
    assert ctx.revision == 2
    assert ctx.excluded_ids == [17]
    assert [p.source for p in ctx.base_points] == ["agent", "mine"]
    assert ctx.base_points[1].pinned is True


def test_context_point_needs_coordinates():
    with pytest.raises(ValidationError):
        RouteContext.model_validate({"base_points": [{"id": 1, "name": "X"}]})


def test_excluded_stops_do_not_come_back():
    pool = [_cand(1, "Форт №16"), _cand(2, "Кафе Немо"), _cand(3, "Туалет")]
    kept = _drop_excluded(pool, {1})
    assert [c.name for c in kept] == ["Кафе Немо", "Туалет"]


def test_exclusion_is_a_no_op_when_nothing_was_deleted():
    pool = [_cand(1, "Новый замок"), _cand(2, "Старый замок")]
    assert len(_drop_excluded(pool, set())) == 2


def test_base_stops_come_back_after_every_trim():
    """A refinement adds: the stops from the previous turn survive retrieval,
    geo focus and the diversity trim."""
    pool = [_cand(7, "Кафе Немо")]
    rows = [
        {"id": 1, "name": "Старый замок", "category": "замок", "lat": 53.67, "lon": 23.82},
        {"id": 2, "name": "Новый замок", "category": "дворец", "lat": 53.67, "lon": 23.82},
    ]

    merged, base = _with_base_points(pool, rows, set())

    assert [c.id for c in merged] == [7, 1, 2]
    assert [c.name for c in base] == ["Старый замок", "Новый замок"]
    assert base[0].category == "замок"


def test_base_stop_the_user_deleted_is_not_resurrected():
    pool = [_cand(7, "Кафе Немо")]
    rows = [{"id": 1, "name": "Форт №16", "category": "инфраструктура", "lat": 53.6, "lon": 23.8}]

    merged, base = _with_base_points(pool, rows, {1})

    assert [c.id for c in merged] == [7]
    assert base == []


def test_base_stop_already_in_the_pool_is_not_duplicated():
    pool = [_cand(1, "Старый замок")]
    rows = [{"id": 1, "name": "Старый замок", "category": "замок", "lat": 53.67, "lon": 23.82}]

    merged, base = _with_base_points(pool, rows, set())

    assert [c.id for c in merged] == [1]
    assert len(base) == 1


def test_changes_report_what_the_refinement_did():
    base = [_cand(1, "Старый замок"), _cand(2, "Музей быта")]
    route = [_cand(1, "Старый замок"), _cand(9, "Туалет")]

    changes = _context_changes(base, route)

    assert [c.name for c in changes.added] == ["Туалет"]
    assert [c.name for c in changes.removed] == ["Музей быта"]
    assert changes.kept == 1
    assert changes.removed[0].reason


def test_valhalla_pool_keeps_base_stops_and_stays_under_20():
    """Valhalla refuses more than 20 locations (error 150) — that was the 422 on a
    merged museum+café pool."""
    base = [_cand(i, f"монастырь {i}") for i in range(1, 13)]
    extras = [_cand(100 + i, f"кафе {i}") for i in range(30)]
    for e in extras:
        e.rerank_score = 0.5

    capped = _cap_for_valhalla(base + extras, base)

    assert len(capped) == 20
    ids = [c.id for c in capped]
    assert all(b.id in ids for b in base)          # the route the user has survives
    assert ids[:12] == [b.id for b in base]


def test_pool_is_left_alone_when_it_already_fits():
    pool = [_cand(1, "монастырь"), _cand(2, "кафе")]
    assert len(_cap_for_valhalla(pool, pool)) == 2


def test_convenience_stops_come_from_the_neighbourhood(monkeypatch):
    """Cafés are looked up around each stop of the existing route (with a short
    radius), so a refine never drags the walk to a café kilometres away."""
    base = [_cand(1, "монастырь"), _cand(2, "храм")]
    calls: list[tuple[float, float, float]] = []

    def fake_nearby(db, lat, lon, radius_km=12.0, limit=50):
        calls.append((lat, lon, radius_km))
        return [
            {"id": 50 + len(calls), "name": "Кафе рядом", "category": "кафе",
             "lat": lat, "lon": lon},
        ]

    monkeypatch.setattr("agent.planner.pipeline.nearby_places", fake_nearby)

    found = _nearby_convenience(None, base, {"кафе"})

    # one lookup per stop, each with the small convenience radius
    assert calls == [
        (base[0].lat, base[0].lon, constants.CONVENIENCE_RADIUS_M / 1000.0),
        (base[1].lat, base[1].lon, constants.CONVENIENCE_RADIUS_M / 1000.0),
    ]
    assert [c.name for c in found] == ["Кафе рядом", "Кафе рядом"]


def test_convenience_search_ignores_categories_nobody_asked_for(monkeypatch):
    base = [_cand(1, "монастырь")]

    def fake_nearby(db, lat, lon, radius_km=12.0, limit=50):
        return [
            {"id": 60, "name": "Гостиница", "category": "гостиница", "lat": lat, "lon": lon},
            {"id": 61, "name": "Туалет", "category": "туалет", "lat": lat, "lon": lon},
        ]

    monkeypatch.setattr("agent.planner.pipeline.nearby_places", fake_nearby)

    found = _nearby_convenience(None, base, {"туалет"})

    assert [c.name for c in found] == ["Туалет"]

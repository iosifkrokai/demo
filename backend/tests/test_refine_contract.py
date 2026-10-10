"""The route-refinement contract: honoured or honestly refused, never a crash."""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.planner import (
    cost as cost_mod,
    pipeline as pipeline_mod,
    refine as refine_mod,
    resolve as resolve_mod,
)
from agent.planner.pipeline import (
    Pipeline,
    _refinement_cost,
    _synthetic_cost,
)
from agent.planner.refine import (
    REFINEMENT_REORDER_ATTRIBUTE_MISSING,
    REFINEMENT_UNRECOGNIZED,
    REFINEMENT_UNSUPPORTED,
    interpret_refinement,
    is_excluded_category,
    reorder_stops,
    visit_minutes_of,
)
from api import main as agent_main
from contracts.planner import Candidate, GenerateReq, LatLon
from core.errors import UpstreamUnavailable


@pytest.fixture
def reading(fake_llm):
    """A model reading so a refinement turn can reach the contract.

    Reading the free text is the model's job; an empty reading is enough here —
    these tests exercise the refinement operation, not the interpreter.
    """
    fake_llm.set()
    return fake_llm


def _cand(
    pid: int,
    name: str,
    category: str = "замок",
    lat: float = 53.678,
    lon: float = 23.828,
    visit: int | None = None,
) -> Candidate:
    return Candidate(
        id=pid, name=name, category=category, lat=lat, lon=lon,
        visit_minutes_db=visit,
    )


def _row(
    pid: int,
    name: str,
    category: str = "замок",
    lat: float = 53.678,
    lon: float = 23.828,
    visit: int | None = None,
) -> dict:
    return {
        "id": pid, "name": name, "category": category, "lat": lat, "lon": lon,
        "visit_minutes": visit,
    }


BASE_ROWS = [
    _row(1, "Старый замок (Гродно)", "замок", 53.6771, 23.8290, 90),
    _row(12, "Музей истории города Гродно", "музей", 53.6783, 23.8265, 45),
    _row(7, "Ансамбль Городницы", "архитектура", 53.6849, 23.8310, 45),
]


def _patch_offline(monkeypatch, rows=None, nearby=None):
    """Wire the refinement path to fakes: no DB, no Valhalla, no retrieval."""
    rows = BASE_ROWS if rows is None else rows
    by_id = {r["id"]: r for r in rows}
    monkeypatch.setattr(
        pipeline_mod, "fetch_points_by_ids",
        lambda db, ids: [by_id[i] for i in ids if i in by_id],
    )
    monkeypatch.setattr(
        pipeline_mod, "nearby_places",
        lambda db, lat, lon, radius_km=12.0, limit=50: list(nearby or []),
    )
    monkeypatch.setattr(
        refine_mod, "nearby_places",
        lambda db, lat, lon, radius_km=12.0, limit=50: list(nearby or []),
    )
    monkeypatch.setattr(
        cost_mod, "compute_cost_matrix",
        lambda *_a, **_kw: (_ for _ in ()).throw(UpstreamUnavailable("no valhalla")),
    )
    monkeypatch.setattr(
        pipeline_mod, "render",
        lambda *_a, **_kw: (_ for _ in ()).throw(UpstreamUnavailable("no valhalla")),
    )
    monkeypatch.setattr(
        pipeline_mod, "retrieve",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("a refinement must not run region-wide retrieval")
        ),
    )
    monkeypatch.setattr(
        pipeline_mod, "optimize",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("a refinement must not re-plan the trip")
        ),
    )
    monkeypatch.setattr(
        resolve_mod, "_name_match_search", lambda db, name, limit=5: []
    )
    monkeypatch.setattr(
        resolve_mod, "_keyword_search", lambda db, q, limit=5: []
    )


class _FakeDB:
    """Only ever reached through monkeypatched helpers."""


def _refine(monkeypatch, instruction, base_ids=(1, 12, 7),
            excluded=None, origin=None, query="Гродно, замки, 2 часа"):
    _patch_offline(monkeypatch, rows=[r for r in BASE_ROWS if r["id"] in base_ids])
    ctx: dict = {
        "revision": 3,
        "base_points": [
            {"id": i, "name": f"stop {i}", "lat": 53.678, "lon": 23.828}
            for i in base_ids
        ],
    }
    if instruction is not None:
        ctx["instruction"] = instruction
    if excluded:
        ctx["excluded_ids"] = list(excluded)
    body: dict = {"query": query, "context": ctx}
    if origin is not None:
        body["origin"] = origin
    req = GenerateReq.model_validate(body)
    return Pipeline(db=_FakeDB()).generate(req)  # type: ignore[arg-type]


class TestInterpretRefinement:

    def test_empty_instruction_is_a_keep_noop(self):
        assert interpret_refinement(None).operation == "none"
        assert interpret_refinement("   ").operation == "none"

    def test_add_categories(self):
        plan = interpret_refinement("добавь кофейню и туалет")
        assert plan.operation == "add"
        assert plan.add_categories == ("кафе", "туалет")
        assert plan.exclude_categories == ()

    def test_exclude_category_without(self):
        plan = interpret_refinement("без музеев")
        assert plan.operation == "remove"
        assert plan.exclude_categories == ("музей",)
        assert plan.add_categories == ()
        assert plan.cats == ("музей",)

    def test_exclude_category_imperative(self):
        for text, code in [
            ("исключи кафе", "кафе"),
            ("без кафе", "кафе"),
            ("исключи музеи", "музей"),
            ("исключи музей", "музей"),
            ("без музеев", "музей"),
        ]:
            plan = interpret_refinement(text)
            assert plan.operation == "remove", text
            assert plan.exclude_categories == (code,), text

    def test_remove_named_stop(self):
        plan = interpret_refinement("убери форт")
        assert plan.operation == "remove"
        assert "форт" in plan.remove_names
        assert plan.exclude_categories == ()

    def test_mixed_add_and_exclude_is_classified_by_the_nearest_verb(self):
        plan = interpret_refinement("добавь кафе и убери музеи")
        assert plan.operation == "add"
        assert plan.add_categories == ("кафе",)
        assert plan.exclude_categories == ("музей",)
        assert "музей" not in plan.add_categories

    def test_excluded_category_is_never_added(self):
        plan = interpret_refinement("без музеев")
        assert "музей" not in plan.add_categories
        assert plan.exclude_categories == ("музей",)

    def test_reorder_by_visit_time(self):
        plan = interpret_refinement("отсортируй по времени посещения")
        assert plan.operation == "reorder"
        assert plan.reorder_by == "visit_minutes"
        assert plan.descending is False

    def test_reorder_longest_first(self):
        plan = interpret_refinement("сначала самые длинные")
        assert (plan.operation, plan.reorder_by, plan.descending) == (
            "reorder", "visit_minutes", True,
        )

    def test_reorder_by_distance(self):
        plan = interpret_refinement("упорядочи по расстоянию от старта")
        assert plan.operation == "reorder"
        assert plan.reorder_by == "distance"

    def test_unsupported_mutation_is_refused_with_a_code(self):
        for text in ["сделай маршрут короче", "построй другой маршрут",
                     "оптимизируй маршрут", "переведи на английский"]:
            plan = interpret_refinement(text)
            assert plan.operation == "unsupported", text
            assert plan.reason_code is not None
            assert plan.supported is False
            assert plan.detail

    def test_reorder_without_attribute_is_refused_not_guessed(self):
        plan = interpret_refinement("начни с замка")
        assert plan.operation == "unsupported"
        assert plan.reason_code == REFINEMENT_REORDER_ATTRIBUTE_MISSING

    def test_unknown_instruction_is_refused_not_ignored(self):
        plan = interpret_refinement("что-нибудь эдакое")
        assert plan.operation == "unsupported"
        assert plan.reason_code == REFINEMENT_UNRECOGNIZED


class TestReorder:

    STOPS = [
        _cand(1, "Замок", "замок", 53.60, 23.80, 90),
        _cand(2, "Кафе", "кафе", 53.61, 23.81, 10),
        _cand(3, "Музей", "музей", 53.62, 23.82, 45),
    ]

    def test_by_visit_minutes_ascending(self):
        out = reorder_stops(self.STOPS, by="visit_minutes")
        assert [c.name for c in out] == ["Кафе", "Музей", "Замок"]

    def test_by_visit_minutes_descending(self):
        out = reorder_stops(self.STOPS, by="visit_minutes", descending=True)
        assert [c.name for c in out] == ["Замок", "Музей", "Кафе"]

    def test_visit_minutes_from_the_stop_own_data(self):
        assert visit_minutes_of(_cand(1, "X", "замок", visit=123)) == 123
        assert visit_minutes_of(_cand(1, "X", "замок")) == 40

    def test_reorder_keeps_every_stop(self):
        out = reorder_stops(self.STOPS, by="visit_minutes")
        assert sorted(c.id for c in out) == [1, 2, 3]

    def test_by_distance_from_the_origin(self):
        origin = LatLon(lat=53.621, lon=23.821)
        out = reorder_stops(self.STOPS, by="distance", origin=origin)
        assert [c.name for c in out] == ["Музей", "Кафе", "Замок"]

    def test_by_distance_uses_the_road_matrix_when_supplied(self):
        matrix = [
            [0.0, 500.0, 100.0],
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
        ]
        out = reorder_stops(self.STOPS, by="distance", matrix=matrix)
        assert [c.name for c in out] == ["Замок", "Музей", "Кафе"]

    def test_reorder_is_stable_for_equal_keys(self):
        stops = [_cand(1, "A", "замок", visit=20), _cand(2, "B", "кафе", visit=20)]
        assert [c.name for c in reorder_stops(stops, by="visit_minutes")] == ["A", "B"]


class TestExcludedCategory:

    def test_matches_canonical_code(self):
        assert is_excluded_category(_cand(1, "X", "музей"), ("музей",))
        assert not is_excluded_category(_cand(1, "X", "замок"), ("музей",))

    def test_matches_a_verbose_category_string(self):
        assert is_excluded_category(
            _cand(1, "X", "католический костёл"), ("костёл",)
        )


class TestRefinementKeepsTheBaseRoute:

    def test_no_instruction_keeps_the_previous_route(self, reading, monkeypatch):
        resp = _refine(monkeypatch, None)
        assert [p.id for p in resp.points] == [1, 12, 7]
        assert resp.debug["refinement"]["operation"] == "none"
        assert resp.changes.kept == 3
        assert resp.changes.added == []

    def test_three_base_points_do_not_422(self, reading, monkeypatch):
        """The exact bug: 3 base_points + a refinement used to answer
        HTTP 422 'optimizer could not produce a route with ≥ 2 stops'."""
        for instruction in [None, "сделай маршрут короче",
                            "отсортируй по времени посещения"]:
            resp = _refine(monkeypatch, instruction)
            assert len(resp.points) == 3, instruction
            assert {p.id for p in resp.points} == {1, 12, 7}

    def test_reorder_by_visit_time_changes_the_order_only(
        self, reading, monkeypatch
    ):
        resp = _refine(monkeypatch, "отсортируй по времени посещения")
        assert [p.id for p in resp.points] == [12, 7, 1]
        assert resp.debug["refinement"]["reorder_by"] == "visit_minutes"

    def test_reorder_longest_first(self, reading, monkeypatch):
        resp = _refine(monkeypatch, "сначала самые длинные")
        assert [p.id for p in resp.points] == [1, 12, 7]

    def test_reorder_by_distance_from_origin(self, reading, monkeypatch):
        origin = LatLon(lat=53.6785, lon=23.8266)
        resp = _refine(monkeypatch, "по расстоянию от старта", origin=origin)
        assert resp.points[0].id == 12
        assert resp.debug["refinement"]["reorder_by"] == "distance"

    def test_exclude_category_removes_matching_stops(self, reading, monkeypatch):
        resp = _refine(monkeypatch, "без музеев")
        assert [p.id for p in resp.points] == [1, 7]
        assert [c.name for c in resp.changes.removed] == ["Музей истории города Гродно"]
        assert resp.debug["refinement"]["exclude_categories"] == ["музей"]

    def test_excluded_ids_never_come_back(self, reading, monkeypatch):
        resp = _refine(monkeypatch, None, excluded=[12])
        assert 12 not in {p.id for p in resp.points}
        assert resp.changes.kept == 2

    def test_add_pulls_cafes_by_the_route_and_keeps_the_base(
        self, reading, monkeypatch
    ):
        cafe = _row(500, "Кафе рядом", "кафе", 53.6785, 23.8285, 40)
        _patch_offline(monkeypatch, nearby=[cafe])
        ctx = {
            "revision": 1,
            "instruction": "добавь кофейню и туалет",
            "base_points": [
                {"id": i, "name": f"stop {i}", "lat": 53.678, "lon": 23.828}
                for i in (1, 12, 7)
            ],
        }
        resp = Pipeline(db=_FakeDB()).generate(  # type: ignore[arg-type]
            GenerateReq.model_validate({"query": "Гродно, замки", "context": ctx})
        )
        ids = [p.id for p in resp.points]
        assert ids[:3] == [1, 12, 7]
        assert 500 in ids
        assert [c.name for c in resp.changes.added] == ["Кафе рядом"]

    def test_unsupported_leaves_the_route_intact_with_a_reason_code(
        self, reading, monkeypatch
    ):
        resp = _refine(monkeypatch, "сделай маршрут короче")
        assert [p.id for p in resp.points] == [1, 12, 7]
        refine = resp.debug["refinement"]
        assert refine["operation"] == "unsupported"
        assert refine["supported"] is False
        assert refine["reason_code"] == REFINEMENT_UNSUPPORTED
        assert refine["detail"]
        assert resp.explanation == refine["detail"]
        assert resp.changes.added == []
        assert resp.changes.removed == []


class TestRefinementOverHttp:

    @pytest.fixture
    def client(self, reading, monkeypatch, _restore_planner):
        _patch_offline(monkeypatch)
        planner = Pipeline(db=_FakeDB())  # type: ignore[arg-type]
        agent_main.app.state.planner = planner
        return TestClient(agent_main.app, raise_server_exceptions=False)

    def test_three_base_points_refinement_is_200(self, client):
        body = {
            "query": "Гродно, замки, 2 часа",
            "context": {
                "revision": 1,
                "instruction": "отсортируй по времени посещения",
                "base_points": [
                    {"id": i, "name": f"stop {i}", "lat": 53.678, "lon": 23.828}
                    for i in (1, 12, 7)
                ],
            },
        }
        r = client.post("/routes/generate", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert [p["id"] for p in data["points"]] == [12, 7, 1]
        assert data["debug"]["refinement"]["reason_code"] is None

    def test_unsupported_refinement_is_200_with_a_code(self, client):
        body = {
            "query": "Гродно, замки, 2 часа",
            "context": {
                "revision": 2,
                "instruction": "сделай маршрут короче",
                "base_points": [
                    {"id": i, "name": f"stop {i}", "lat": 53.678, "lon": 23.828}
                    for i in (1, 12, 7)
                ],
            },
        }
        r = client.post("/routes/generate", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert [p["id"] for p in data["points"]] == [1, 12, 7]
        assert data["debug"]["refinement"]["reason_code"] == REFINEMENT_UNSUPPORTED


class TestCostFallback:

    def test_synthetic_cost_has_no_unreachable_cells(self):
        cands = [_cand(1, "A", "замок"), _cand(2, "B", "кафе", 53.70, 23.85)]
        cost = _synthetic_cost(cands)
        assert len(cost.walk_seconds) == 2
        assert cost.walk_seconds[0][1] > 0
        assert cost.indices == [0, 1]
        assert cost.visit_minutes == [40, 40]

    def test_refinement_cost_never_drops_a_base_stop(self, monkeypatch):
        cands = [_cand(1, "A"), _cand(2, "B", lat=53.70, lon=23.85)]
        monkeypatch.setattr(
            cost_mod, "compute_cost_matrix",
            lambda *_a, **_kw: (_ for _ in ()).throw(UpstreamUnavailable("down")),
        )
        route, cost = _refinement_cost(cands, constraints=None, costing="pedestrian")  # type: ignore[arg-type]
        assert [c.id for c in route] == [1, 2]
        assert len(cost.walk_seconds) == 2


@pytest.fixture
def _restore_planner():
    yield
    if hasattr(agent_main.app.state, "planner"):
        delattr(agent_main.app.state, "planner")

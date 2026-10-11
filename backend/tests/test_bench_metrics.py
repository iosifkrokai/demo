"""Metric arithmetic for quality/runner.py.

No network and no agent: every case is made-up coordinates or a monkeypatched call.
"""

from __future__ import annotations

import math
import os
import sys
from itertools import permutations

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from quality import runner as b

KM_PER_DEG_LON = 2 * math.pi * b._R / 360.0


def pt(east_km: float) -> tuple[float, float]:
    """A point `east_km` kilometres east of (0.0, 0.0)."""
    return (0.0, east_km / KM_PER_DEG_LON)


def stops(*east_km: float) -> list[b.GoldenStop]:
    return [b.GoldenStop(f"s{i}", lat, lon) for i, (lat, lon) in enumerate(map(pt, east_km))]


def our_stops(*east_km: float) -> list[b.OurStop]:
    return [b.OurStop(f"o{i}", lat, lon) for i, (lat, lon) in enumerate(map(pt, east_km))]


def api_points(names: list[str], *east_km: float) -> list[dict]:
    """Points in the shape /routes/generate returns them."""
    assert len(names) == len(east_km)
    return [
        {"name": nm, "lat": lat, "lon": lon, "visit_minutes": 10}
        for nm, (lat, lon) in zip(names, map(pt, east_km), strict=True)
    ]


def test_reference_walk_is_the_shortest_order_not_the_file_order():
    ref = b.build_reference_walk(stops(0.0, 10.0, 1.0, 11.0))

    assert ref.order == [0, 2, 1, 3]
    assert ref.distance_km == pytest.approx(11.0, abs=1e-6)
    assert ref.article_distance_km == pytest.approx(29.0, abs=1e-6)
    assert ref.article_distance_km > ref.distance_km


def test_reference_walk_beats_every_other_ordering():
    pts = [pt(0.0), pt(10.0), pt(1.0), pt(11.0)]
    got = b.walk_distance_km(pts, b.shortest_walk_order(pts))
    for order in permutations(range(len(pts))):
        assert got <= b.walk_distance_km(pts, list(order)) + 1e-9


def test_reference_walk_on_the_committed_mir_stops():
    """Real committed coordinates: the .json order is 1.252 km, shortest 0.795."""
    ref = b.build_reference_walk(
        [
            b.GoldenStop("Mir castle", 53.4510, 26.4722),
            b.GoldenStop("Mir synagogue", 53.4505, 26.4795),
            b.GoldenStop("Mir Trinity church", 53.4498, 26.4680),
        ]
    )
    assert ref.order == [1, 0, 2]
    assert ref.order != [0, 1, 2]
    assert ref.article_distance_km == pytest.approx(1.252, abs=0.002)
    assert ref.distance_km == pytest.approx(0.795, abs=0.002)
    assert ref.article_distance_km / ref.distance_km == pytest.approx(1.575, abs=0.01)


def test_shortest_walk_order_is_deterministic_and_tie_broken():
    pts = [pt(0.0), pt(1.0), pt(2.0), pt(3.0)]
    first = b.shortest_walk_order(pts)
    assert first == [0, 1, 2, 3]
    for _ in range(5):
        assert b.shortest_walk_order(pts) == first


def test_shortest_walk_order_handles_degenerate_sizes():
    assert b.shortest_walk_order([]) == []
    assert b.shortest_walk_order([pt(0.0)]) == [0]
    assert b.shortest_walk_order([pt(0.0), pt(1.0)]) == [0, 1]


def test_shortest_walk_order_falls_back_above_the_exact_dp_limit():
    n = b.REF_WALK_EXACT_MAX_STOPS + 1
    order = b.shortest_walk_order([pt(float(i)) for i in range(n)])
    assert sorted(order) == list(range(n))


def test_tau_identical_order_is_plus_one():
    assert b.kendall_tau([4, 7, 9], [4, 7, 9]) == pytest.approx(1.0)


def test_tau_fully_reversed_order_is_minus_one():
    assert b.kendall_tau([4, 7, 9], [9, 7, 4]) == pytest.approx(-1.0)


def test_tau_two_inversions_of_three_is_minus_one_third():
    assert b.kendall_tau([4, 7, 9], [7, 9, 4]) == pytest.approx(-1.0 / 3.0)


def test_tau_one_inversion_of_three_is_plus_one_third():
    assert b.kendall_tau([4, 7, 9], [7, 4, 9]) == pytest.approx(1.0 / 3.0)


def test_tau_four_stops_one_rotation_is_zero():
    assert b.kendall_tau([10, 20, 30, 40], [20, 30, 40, 10]) == pytest.approx(0.0)


def test_tau_four_stops_known_fraction():
    assert b.kendall_tau([10, 20, 30, 40], [20, 30, 10, 40]) == pytest.approx(1.0 / 3.0)


def test_tau_is_symmetric():
    a = [30, 10, 40, 20]
    c = [20, 40, 10, 30]
    assert b.kendall_tau(a, c) == pytest.approx(b.kendall_tau(c, a))
    assert b.kendall_tau(a, c) == pytest.approx(-1.0)


def test_tau_uses_only_the_shared_subset():
    tau_all_shared = b.kendall_tau([1, 2, 3], [1, 2, 3])
    assert tau_all_shared == pytest.approx(1.0)

    ref_order = [9, 1, 8, 2, 7, 3]
    our_order = [8, 3, 9, 1, 7, 2]
    only_shared_ref = [i for i in ref_order if i in (1, 2, 3)]
    only_shared_our = [i for i in our_order if i in (1, 2, 3)]
    assert only_shared_ref == [1, 2, 3]
    assert only_shared_our == [3, 1, 2]
    assert b.kendall_tau(only_shared_ref, only_shared_our) == pytest.approx(-1.0 / 3.0)


def test_tau_is_na_below_the_minimum_shared_stops():
    assert b.MIN_TAU_STOPS == 3
    assert b.kendall_tau([4, 7], [4, 7]) is None
    assert b.kendall_tau([4, 7], [7, 4]) is None
    assert b.kendall_tau([4], [4]) is None
    assert b.kendall_tau([], []) is None
    assert b.kendall_tau([1, 2, 3, 4], [1, 90, 80]) is None


def test_tau_is_defined_exactly_at_the_minimum():
    ids = [1, 2, 3]
    assert len(ids) == b.MIN_TAU_STOPS
    assert b.kendall_tau(ids, list(ids)) == pytest.approx(1.0)
    assert b.kendall_tau(ids, [3, 2, 1]) == pytest.approx(-1.0)


def test_detour_is_our_length_minus_the_reference_length():
    assert b.detour_km(2.5, 1.0) == pytest.approx(1.5)
    assert b.detour_km(1.0, 2.5) == pytest.approx(-1.5)
    assert b.detour_km(1.234, 1.234) == pytest.approx(0.0)


def test_detour_is_none_when_either_side_is_unknown():
    assert b.detour_km(None, 1.0) is None
    assert b.detour_km(1.0, None) is None
    assert b.detour_km(None, None) is None


def test_detour_from_two_explicit_walks():
    ref_km = b.walk_distance_km([pt(0.0), pt(1.0), pt(2.0)], [0, 1, 2])
    our_km = b.walk_distance_km([pt(0.0), pt(2.0), pt(1.0)], [0, 1, 2])

    assert ref_km == pytest.approx(2.0, abs=1e-6)
    assert our_km == pytest.approx(3.0, abs=1e-6)
    assert b.detour_km(our_km, ref_km) == pytest.approx(1.0, abs=1e-6)


def test_detour_is_zero_when_our_walk_equals_the_reference_walk():
    pts = [pt(0.0), pt(10.0), pt(1.0), pt(11.0)]
    ref = b.build_reference_walk(stops(0.0, 10.0, 1.0, 11.0))
    ours = [pts[i] for i in ref.order]

    assert b.detour_km(
        b.walk_distance_km(ours, list(range(len(ours)))), ref.distance_km
    ) == pytest.approx(0.0, abs=1e-9)


def test_detour_uses_the_same_measure_on_both_sides():
    """Network length is reported, but never mixed into the detour delta.

    Our Valhalla network distance has no offline counterpart, so detour is haversine.
    """
    ref = b.build_reference_walk(stops(0.0, 10.0, 1.0, 11.0))
    assert b.detour_km(11.0, ref.distance_km) == pytest.approx(0.0, abs=1e-9)
    assert b.detour_km(11.0 * 1.4, ref.distance_km) == pytest.approx(4.4, abs=1e-6)


def test_spread_is_the_half_range():
    assert b._spread([]) is None
    assert b._spread([2.0]) == pytest.approx(0.0)
    assert b._spread([1.0, 1.0, 1.0]) == pytest.approx(0.0)
    assert b._spread([1.0, 2.0, 3.0]) == pytest.approx(1.0)
    assert b._spread([5.0, 5.0, 1.0]) == pytest.approx(2.0)


def test_mean_ignores_undefined_runs():
    assert b._mean([]) is None
    assert b._mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)


def _run(**kw):
    return b.EvaluationResult(golden=b.GoldenRoute("r", "s", "q", 60, []), **kw)


def test_ms_renders_mean_plus_minus_spread():
    runs = [_run(kendall_tau=0.5), _run(kendall_tau=None), _run(kendall_tau=1.0)]
    assert b._ms(runs, "kendall_tau", 2) == "0.75±0.25"
    assert b._ms([_run(kendall_tau=0.5)], "kendall_tau", 3) == "0.500±0.000"
    assert b._ms([_run(kendall_tau=None), _run(kendall_tau=None)], "kendall_tau", 2) == "n/a"


def test_fit_cell_counts_runs_that_fit():
    runs = [_run(budget_fit=True), _run(budget_fit=False), _run(budget_fit=True)]
    assert b._fit_cell(runs) == "2/3"
    assert b._fit_cell([_run(budget_fit=True)]) == "1/1"


def _fake_generate(points, walk_s=1800.0, length_km=None, fits=True):
    def _call(base_url, query, budget_minutes, origin_lat, origin_lon):
        return {
            "points": [
                {
                    "name": p["name"],
                    "lat": p["lat"],
                    "lon": p["lon"],
                    "visit_minutes": p.get("visit_minutes"),
                }
                for p in points
            ],
            "summary": {"time_seconds": walk_s, "length_km": length_km},
            "budget": {
                "walk_minutes": int(walk_s / 60),
                "visit_minutes": 0,
                "total_minutes": int(walk_s / 60),
                "fits": fits,
            },
        }

    return _call


def test_evaluate_scores_a_perfect_route_against_the_reference_walk(
    monkeypatch,
):
    """The regression this whole change exists for.

    Our route walks the reference stops in the shortest possible order, so it scores 1.0.
    """
    route = b.GoldenRoute(
        name="synthetic",
        source="unit test",
        query_ru="q",
        budget_minutes=120,
        stops=stops(0.0, 10.0, 1.0, 11.0),
        est_walk_minutes=20,
    )
    perfect = api_points(["a", "b", "c", "d"], 0.0, 1.0, 10.0, 11.0)
    monkeypatch.setattr(b, "call_generate", _fake_generate(perfect))

    r = b.evaluate(route, "http://unused")

    assert r.api_error is None
    assert r.shared_stops == 4
    assert r.kendall_tau == pytest.approx(1.0)
    assert r.our_walk_km == pytest.approx(r.ref_walk_km, abs=1e-6)
    assert r.detour_km == pytest.approx(0.0, abs=1e-6)
    assert r.recall_at_k == pytest.approx(1.0)
    assert r.precision == pytest.approx(1.0)
    assert r.budget_fit is True
    assert r.walk_diff_min == pytest.approx(30.0 - 20.0)


def test_evaluate_reports_n_a_tau_when_too_few_stops_are_shared(
    monkeypatch,
):
    route = b.GoldenRoute(
        name="synthetic",
        source="unit test",
        query_ru="q",
        budget_minutes=120,
        stops=stops(0.0, 1.0, 2.0, 3.0),
        est_walk_minutes=10,
    )
    ours = api_points(["a", "b"], 0.0, 3.0)
    monkeypatch.setattr(b, "call_generate", _fake_generate(ours))

    r = b.evaluate(route, "http://unused")

    assert r.shared_stops == 2
    assert r.kendall_tau is None
    assert r.recall_at_k == pytest.approx(0.5)
    assert r.precision == pytest.approx(1.0)


def test_evaluate_detour_grows_with_a_worse_our_order(monkeypatch):
    """detour is monotone in how much we backtrack, and needs no reference."""
    route = b.GoldenRoute(
        name="synthetic",
        source="unit test",
        query_ru="q",
        budget_minutes=120,
        stops=stops(0.0, 1.0, 2.0, 3.0),
        est_walk_minutes=10,
    )
    straight = api_points(["a", "b", "c", "d"], 0.0, 1.0, 2.0, 3.0)
    shuffled = [straight[i] for i in (0, 3, 1, 2)]

    monkeypatch.setattr(b, "call_generate", _fake_generate(straight))
    good = b.evaluate(route, "http://unused")
    monkeypatch.setattr(b, "call_generate", _fake_generate(shuffled))
    bad = b.evaluate(route, "http://unused")

    assert good.detour_km == pytest.approx(0.0, abs=1e-6)
    assert bad.detour_km > good.detour_km
    assert good.kendall_tau == pytest.approx(1.0)
    assert bad.kendall_tau is not None and bad.kendall_tau < 1.0
    assert good.recall_at_k == bad.recall_at_k == pytest.approx(1.0)


def test_evaluate_carries_the_network_length_separately(monkeypatch):
    route = b.GoldenRoute(
        name="synthetic",
        source="unit test",
        query_ru="q",
        budget_minutes=120,
        stops=stops(0.0, 1.0, 2.0),
        est_walk_minutes=10,
    )
    ours = api_points(["a", "b", "c"], 0.0, 1.0, 2.0)
    monkeypatch.setattr(b, "call_generate", _fake_generate(ours, length_km=4.2))

    r = b.evaluate(route, "http://unused")

    assert r.our_walk_km == pytest.approx(2.0, abs=1e-6)
    assert r.our_walk_km_net == pytest.approx(4.2)
    assert r.detour_km == pytest.approx(0.0, abs=1e-6)


def test_evaluate_survives_an_api_error(monkeypatch):
    route = b.GoldenRoute(
        name="synthetic",
        source="unit test",
        query_ru="q",
        budget_minutes=120,
        stops=stops(0.0, 1.0),
        est_walk_minutes=10,
    )

    def _boom(*a, **k):
        raise b.urllib.error.URLError("connection refused")

    monkeypatch.setattr(b, "call_generate", _boom)

    r = b.evaluate(route, "http://unused")

    assert r.api_error is not None
    assert r.kendall_tau is None
    assert r.detour_km is None
    assert math.isfinite(r.latency_s)

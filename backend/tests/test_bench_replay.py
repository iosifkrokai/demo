"""Freeze-and-replay harness for quality/runner.py.

No network or agent: responses are hand-built, so every number derives by hand.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from quality import runner as b

KM_PER_DEG_LON = 2 * math.pi * b._R / 360.0


def pt(east_km: float) -> tuple[float, float]:
    return (0.0, east_km / KM_PER_DEG_LON)


def km_east(lat: float, lon: float) -> float:
    """How far east a point is, in km. Exact at the equator, where all the
    synthetic coordinates live."""
    return lon * KM_PER_DEG_LON


def golden_stops(*east_km: float, grade: str | None = None) -> list[b.GoldenStop]:
    return [
        b.GoldenStop(f"g{i}", lat, lon, 10, grade) for i, (lat, lon) in enumerate(map(pt, east_km))
    ]


def make_golden(
    name: str = "synthetic",
    stops=None,
    *,
    path: Path | None = None,
    case: str = "synthetic",
    budget: int = 120,
) -> b.GoldenRoute:
    return b.GoldenRoute(
        name=name,
        source="unit test",
        query_ru="q",
        budget_minutes=budget,
        stops=stops if stops is not None else golden_stops(0.0, 1.0, 2.0),
        est_walk_minutes=10,
        path=path,
        case=case,
    )


def point(name: str, east_km: float, pid: int | None = None) -> dict:
    lat, lon = pt(east_km)
    out = {"name": name, "lat": lat, "lon": lon, "visit_minutes": 10}
    if pid is not None:
        out["id"] = pid
    return out


def cover_points(golden: b.GoldenRoute, pid: int = 1) -> list[dict]:
    """One returned stop on every reference stop — recall 1.0 by construction."""
    return [point(f"ours{i}", km_east(s.lat, s.lon), pid + i) for i, s in enumerate(golden.stops)]


def api_response(
    points: list[dict],
    *,
    walk_s: float = 1800.0,
    length_km: float | None = 2.0,
    fits: bool = True,
    stops_dropped: int = 0,
    shape: dict | None = "auto",
    trace: dict | None = None,
    intent_source: str = "regex",
) -> dict:
    """A /routes/generate response in the shape the API actually returns."""
    if shape == "auto":
        shape = {"type": "LineString", "coordinates": [[23.8, 53.6]]}
    body = {
        "parsed": {"source": intent_source},
        "points": points,
        "shape": shape,
        "summary": {"length_km": length_km, "time_seconds": walk_s},
        "budget": {
            "budget_minutes": 120,
            "walk_minutes": int(walk_s / 60),
            "visit_minutes": 30,
            "total_minutes": int(walk_s / 60) + 30,
            "fits": fits,
            "stops_dropped": stops_dropped,
        },
        "costing": "pedestrian",
        "debug": {
            "intent_source": intent_source,
            "trace": trace
            if trace is not None
            else {
                "algorithm": "2opt",
                "max_leg_seconds": 600.0,
                "walk_seconds": int(walk_s),
                "total_seconds": int(walk_s) + 1800,
                "budget_seconds": 7200,
                "fits_budget": fits,
            },
        },
    }
    return body


def run_of(golden: b.GoldenRoute, response: dict, repeat: int = 1, **kw) -> b.RunRecord:
    result = b.score_response(golden, response, latency_s=1.5, **kw)
    return b.RunRecord(
        case=golden.case,
        case_name=golden.name,
        repeat=repeat,
        result=result,
        request=b.build_request(golden.query_ru, golden.budget_minutes, 0.0, 0.0),
        response=response,
        ts="2026-09-26T00:00:00+00:00",
        git_sha="deadbee",
    )


def write_snapshot(
    directory: Path,
    groups: list[list[b.RunRecord]],
    *,
    base_url: str = "http://localhost:8080",
    repeat: int = 1,
) -> Path:
    """The same path main() takes: a meta line, then one line per (case, repeat)."""
    cases = [g[0].result.golden for g in groups if g]
    meta = b.make_meta(
        cases=cases,
        base_url=base_url,
        repeat=repeat,
        started_at="2026-09-26T00:00:00+00:00",
        run_id="unit-1",
        git_sha="deadbee",
    )
    path = b.write_meta(meta, directory, append=False)
    for runs in groups:
        for rec in runs:
            b.append_row(b.make_row(rec, base_url), path)
    return path


def golden_file(directory: Path, case: str, name: str, stops: list[dict], **extra) -> Path:
    """Write a quality/cases/routes/<case>.json the loader accepts."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{case}.json"
    payload = {
        "name": name,
        "source": "unit test",
        "query_ru": f"запрос {case}",
        "budget_minutes": 120,
        "est_walk_minutes": 10,
        "stops": stops,
        **extra,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def stop_dict(name: str, east_km: float, grade: str | None = None) -> dict:
    lat, lon = pt(east_km)
    out = {"name": name, "lat": lat, "lon": lon, "visit_minutes": 10}
    if grade is not None:
        out["grade"] = grade
    return out


@pytest.fixture
def routes_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point the harness at synthetic reference files (never the committed ones)."""
    d = tmp_path / "routes"
    golden_file(
        d,
        "alpha",
        "Alpha walk",
        [
            stop_dict("A", 0.0, "must-see"),
            stop_dict("B", 1.0, "nice-to-have"),
            stop_dict("C", 2.0, "must-see"),
        ],
    )
    golden_file(
        d,
        "beta",
        "Beta walk",
        [
            stop_dict("D", 0.0),
            stop_dict("E", 1.5),
        ],
    )
    monkeypatch.setattr(b, "BENCH_ROUTES", d)
    return d


def run_cli(argv: list[str], monkeypatch, capsys) -> str:
    monkeypatch.setattr(sys, "argv", ["bench_routes.py", *argv])
    b.main()
    return capsys.readouterr().out


def test_response_does_not_expose_the_stage1_pool():
    """The honesty premise of the whole split, asserted on a realistic response.

    `retrieve()`'s candidate list never reaches the response, so stage-1 must be a proxy.
    """
    golden = make_golden()
    raw = api_response([point("a", 0.0, 11), point("b", 1.0, 12), point("c", 2.0, 13)])
    result = b.score_response(golden, raw)

    assert b.probe_stage1_pool(raw) is None
    assert result.stage.pool_exposed is False
    assert result.stage.pool_source == "route_points"
    assert result.candidate_ids == [11, 12, 13]
    assert result.candidate_ids_source == "route_points"


def test_pool_probe_would_pick_up_a_pool_if_the_api_ever_exposed_one():
    """Forward compatibility: a pool with coordinates is scored directly.

    The route contains no reference stops, so a non-zero recall proves the pool was used.
    """
    golden = make_golden(stops=golden_stops(0.0, 1.0, 2.0))
    raw = api_response([point("far", 50.0, 11)])
    raw["debug"]["trace"]["candidates"] = [
        {"id": 20 + i, "name": "c", "lat": lat, "lon": lon}
        for i, (lat, lon) in enumerate([pt(0.0), pt(1.0), pt(2.0)])
    ]
    found = b.probe_stage1_pool(raw)
    assert found is not None
    assert found["ids"] == [20, 21, 22]
    assert found["points"] == [pt(0.0), pt(1.0), pt(2.0)]

    result = b.score_response(golden, raw)
    assert result.candidate_ids == [20, 21, 22]
    assert result.candidate_ids_source == "stage1_pool"
    assert result.stage.pool_exposed is True
    assert result.stage.pool_source == "stage1_pool"
    assert result.stage.pool_size == 3
    assert result.stage.n_covered == 3
    assert result.stage.route_recall_given_pool == pytest.approx(0.0)
    assert result.recall_at_k == pytest.approx(0.0)


def test_pool_probe_rejects_ids_without_coordinates_as_unscoreable():
    raw = api_response([point("a", 0.0, 11)])
    raw["debug"]["candidate_ids"] = [1, 2, 3]
    found = b.probe_stage1_pool(raw)
    assert found is not None and found["ids"] == [1, 2, 3]
    assert found["points"] is None


def test_stage1_proxy_uses_a_tighter_radius_than_recall_at_k():
    """The 750 m matcher over-merges; the pool proxy must not inherit that.

    A stop inside 750 m counts for recall@K but not for the 250 m stage-1 proxy.
    """
    golden = make_golden(stops=golden_stops(0.0, 1.0))
    raw = api_response([point("x", 0.0), point("y", 0.7)])
    result = b.score_response(golden, raw)

    assert result.stage.proxy_radius_m == b.STAGE1_PROXY_RADIUS_M == 250.0
    assert result.stage.n_covered == 1
    assert result.stage.recall == pytest.approx(0.5)
    assert result.recall_at_k == pytest.approx(1.0)


def test_stage2_recall_is_conditional_on_a_stage1_hit():
    """stage-2 is the retrieval quality GIVEN, not the whole reference.

    A pool stop the route did not take gives stage 1 = 1.0 and stage 2 = 0.0.
    """
    golden = make_golden(stops=golden_stops(0.0, 1.0))
    raw = api_response([point("a", 0.0, 11), point("b", 0.02, 12)])
    raw["debug"]["trace"]["candidates"] = [
        {"id": 11, "name": "a", "lat": lat, "lon": lon} for lat, lon in (pt(0.0), pt(0.02), pt(1.0))
    ]
    result = b.score_response(golden, raw)

    assert result.stage.n_covered == 2
    assert result.stage.route_recall_given_pool == pytest.approx(0.5)
    assert result.recall_at_k == pytest.approx(0.5)


def test_stage2_recall_is_trivially_one_under_the_route_points_fallback():
    """An honest limitation of the proxy, pinned as a test.

    The fallback pool is a subset of the route, so stage-2 recall is always 1.0.
    """
    golden = make_golden(stops=golden_stops(0.0, 1.0))
    raw = api_response([point("a", 0.0, 11)])
    result = b.score_response(golden, raw)
    assert result.stage.pool_exposed is False
    assert result.stage.n_covered == 1
    assert result.stage.route_recall_given_pool == pytest.approx(1.0)


def test_stage2_recall_is_none_when_nothing_was_in_the_pool():
    golden = make_golden(stops=golden_stops(0.0, 1.0, 2.0))
    raw = api_response([point("x", 50.0)])
    result = b.score_response(golden, raw)
    assert result.stage.n_covered == 0
    assert result.stage.route_recall_given_pool is None


def test_ungraded_reference_stops_count_as_must_see():
    """Strictest reading: no `grade` field means every stop is a must-see."""
    ungraded = b.GoldenStop("s", 0.0, 0.0)
    assert ungraded.grade is None
    assert ungraded.weight == b.GRADE_WEIGHTS[b.DEFAULT_GRADE] == 3.0
    assert b.GoldenStop("s", 0.0, 0.0, grade="nice-to-have").weight == 1.0
    assert b.GoldenStop("s", 0.0, 0.0, grade="available").weight == 0.0


def test_weighted_stage1_recall_respects_grades():
    golden = make_golden(
        stops=[
            b.GoldenStop("a", *pt(0.0), 10, "must-see"),
            b.GoldenStop("b", *pt(1.0), 10, "must-see"),
            b.GoldenStop("c", *pt(2.0), 10, "nice-to-have"),
        ]
    )
    result = b.score_response(golden, api_response([point("x", 1.0), point("y", 2.0)]))
    assert result.stage.recall == pytest.approx(2 / 3)
    assert result.stage.recall_weighted == pytest.approx(4 / 7)


def test_loader_reads_grade_from_the_reference_file(routes_dir):
    routes = b.load_golden_routes(routes_dir)
    by_case = {r.case: r for r in routes}
    assert by_case["alpha"].stops[0].grade == "must-see"
    assert by_case["alpha"].stops[1].grade == "nice-to-have"
    assert all(s.grade is None for s in by_case["beta"].stops)
    assert by_case["beta"].total_weight == pytest.approx(6.0)


DUP_NAME_A = "Костёл Обретения Святого Креста и монастырь бернардинцев"
DUP_NAME_B = "Монастырь бернардинцев и костёл Обретения Креста"
DUP_LAT_A, DUP_LON_A = 53.67481, 23.830602
DUP_LAT_B, DUP_LON_B = 53.6813, 23.8278


def test_the_historical_duplicate_pair_is_a_leg_sanity_failure():
    stops = [
        b.OurStop(DUP_NAME_A, DUP_LAT_A, DUP_LON_A, 20, 1),
        b.OurStop("Улица Советская (старый город)", 53.6798, 23.8279, 40, 2),
        b.OurStop(DUP_NAME_B, DUP_LAT_B, DUP_LON_B, 35, 3),
    ]
    pairs = b.find_duplicate_stop_pairs(stops)

    assert len(pairs) == 1
    assert pairs[0]["our_stop_indices"] == [0, 2]
    assert pairs[0]["rule"] == "same_name_near"
    assert pairs[0]["dist_m"] == pytest.approx(745, abs=2)
    assert pairs[0]["name_similarity"] == pytest.approx(0.857, abs=0.001)


def test_duplicate_pair_surfaces_in_the_metrics_and_the_failure_list():
    golden = make_golden()
    raw = api_response(
        [
            {"name": DUP_NAME_A, "lat": DUP_LAT_A, "lon": DUP_LON_A, "visit_minutes": 20},
            {"name": DUP_NAME_B, "lat": DUP_LAT_B, "lon": DUP_LON_B, "visit_minutes": 35},
        ]
    )
    result = b.score_response(golden, raw)

    assert result.leg.n_duplicate_stops == 1
    assert "duplicate_stop" in result.failure_kinds
    dup = next(f for f in result.failures if f.kind == "duplicate_stop")
    assert dup.gated is False
    assert result.hard_failure is False


def test_duplicate_rules_including_the_plain_coincident_one():
    def stops(a_lat, a_lon, b_lat, b_lon, *, na="Костёл", nb="Музей"):
        return [b.OurStop(na, a_lat, a_lon), b.OurStop(nb, b_lat, b_lon)]

    pair = b.find_duplicate_stop_pairs(stops(53.0, 23.0, 53.0009, 23.0))
    assert pair[0]["rule"] == "coincident"
    pair = b.find_duplicate_stop_pairs(
        stops(53.0, 23.0, 53.0027, 23.0, na="Костёл Святого", nb="костёл святого")
    )
    assert pair[0]["rule"] == "same_name"
    assert b.find_duplicate_stop_pairs(stops(53.0, 23.0, 53.036, 23.0)) == []


def test_unreachable_sentinel_is_read_and_gates_the_run():
    golden = make_golden()
    raw = api_response(
        [point("a", 0.0, 1), point("b", 1.0, 2)],
        trace={
            "algorithm": "2opt",
            "max_leg_seconds": b.UNREACHABLE_S,
            "walk_seconds": b.UNREACHABLE_S,
            "total_seconds": b.UNREACHABLE_S,
        },
    )
    result = b.score_response(golden, raw)

    assert result.leg.unreachable_sentinel is True
    assert result.leg.n_unreachable_legs == 1
    assert "unreachable_leg" in result.failure_kinds
    assert result.hard_failure is True
    assert result.leg.n_unreachable_legs >= 1


def test_a_zero_stop_route_is_a_hard_failure():
    result = b.score_response(make_golden(), api_response([]))
    assert result.failure_kinds == ["zero_stops"]
    assert result.hard_failure is True
    assert b._attr([result], "recall_at_k") == []


def test_an_api_error_is_a_gated_failure_with_the_status():
    result = b.score_response(
        make_golden(), None, http_status=422, api_error="could not produce a route"
    )
    assert result.failure_kinds == ["api_error"]
    assert result.hard_failure is True
    assert result.kendall_tau is None
    assert result.metrics_dict()["http_status"] == 422


def test_a_422_without_a_detail_is_still_a_failure():
    result = b.score_response(make_golden(), None, http_status=503)
    assert result.failure_kinds == ["http_status"]
    assert result.hard_failure is True


def test_missing_geometry_is_a_hard_failure():
    golden = make_golden()
    raw = api_response([point("a", 0.0, 1), point("b", 1.0, 2)], shape={})
    result = b.score_response(golden, raw)
    assert result.leg.geometry_missing is True
    assert "geometry_missing" in result.failure_kinds
    assert result.hard_failure is True


def test_leg_over_cap_is_counted_but_not_gated():
    golden = make_golden()
    raw = api_response(
        [point("a", 0.0, 1), point("b", 1.0, 2)],
        walk_s=600.0,
        length_km=6.0,
        trace={
            "algorithm": "2opt",
            "max_leg_seconds": 720.0,
            "walk_seconds": 600.0,
            "total_seconds": 3000.0,
        },
    )
    result = b.score_response(golden, raw)
    assert result.leg.max_leg_km == pytest.approx(7.2, abs=0.01)
    assert result.leg.over_cap is True
    assert "leg_over_cap" in result.failure_kinds
    assert result.hard_failure is False
    assert result.leg.max_leg_km_straight == pytest.approx(1.0, abs=1e-6)


def test_gated_failures_are_excluded_from_the_quality_means():
    golden = make_golden()
    good = b.score_response(golden, api_response(cover_points(golden)))
    bad = b.score_response(golden, None, http_status=422, api_error="nope")
    assert good.recall_at_k == pytest.approx(1.0)
    assert bad.recall_at_k == pytest.approx(0.0)
    assert b._attr([good, bad], "recall_at_k") == [pytest.approx(1.0)]
    assert b._ms([good, bad], "recall_at_k", 3) == "1.000±0.000"


def test_failure_counts_are_reported_separate_from_the_scores(tmp_path, routes_dir):
    golden = b.load_golden_routes(routes_dir)[0]
    groups = [
        [run_of(golden, api_response([point("a", 0.0, 1), point("b", 1.0, 2)]))],
        [
            b.RunRecord(
                case=golden.case,
                case_name=golden.name,
                repeat=1,
                result=b.score_response(golden, None, http_status=422, api_error="boom"),
            )
        ],
    ]
    summary = b.failure_summary(groups)
    assert summary["total_runs"] == 2
    assert summary["n_failed_runs"] == 1
    assert summary["by_kind"] == {"api_error": 1}
    assert summary["gated"][0]["case"] == golden.case


def test_snapshot_row_carries_request_ids_response_and_metrics(tmp_path):
    golden = make_golden()
    raw = api_response([point("a", 0.0, 11), point("b", 1.0, 12), point("c", 2.0, 13)])
    path = write_snapshot(tmp_path / "snap", [[run_of(golden, raw)]])

    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    meta, row = lines[0], lines[1]
    assert meta["record"] == "meta" and meta["repeat"] == 1
    assert row["record"] == "row" and row["case"] == "synthetic" and row["repeat"] == 1
    assert row["request"] == {
        "query": "q",
        "time_budget_minutes": 120,
        "origin": {"lat": 0.0, "lon": 0.0},
    }
    assert row["candidate_ids"] == [11, 12, 13]
    assert row["candidate_ids_source"] == "route_points"
    assert row["response"] == raw
    assert row["metrics"]["recall_at_k"] == pytest.approx(1.0)
    assert row["http_status"] is None


def test_snapshot_is_one_row_per_case_and_repeat(tmp_path, routes_dir):
    routes = b.load_golden_routes(routes_dir)
    groups = [
        [
            run_of(r, api_response([point("a", 0.0, 1), point("b", 1.0, 2)]), repeat=i + 1)
            for i in range(2)
        ]
        for r in routes
    ]
    path = write_snapshot(tmp_path / "snap", groups, repeat=2)

    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 + 2 * len(routes)
    assert {(r["case"], r["repeat"]) for r in lines[1:]} == {
        ("alpha", 1),
        ("alpha", 2),
        ("beta", 1),
        ("beta", 2),
    }


def test_replay_recomputes_every_metric_without_touching_the_agent(
    tmp_path, routes_dir, monkeypatch
):
    routes = b.load_golden_routes(routes_dir)
    groups = [[run_of(routes[0], api_response([point("a", 0.0, 1), point("b", 1.0, 2)]))]]

    def _boom(*a, **k):
        raise AssertionError("replay must not call the agent")

    monkeypatch.setattr(b, "post_generate", _boom)
    monkeypatch.setattr(b, "call_generate", _boom)

    write_snapshot(tmp_path / "snap", groups)
    replayed, meta, drift = b.run_replay(tmp_path / "snap")

    assert meta["git_sha"] == "deadbee"
    assert drift == []
    assert len(replayed) == 1 and len(replayed[0]) == 1
    live = groups[0][0].result
    again = replayed[0][0].result
    assert again.metrics_dict() == live.metrics_dict()
    assert again.stage.recall == live.stage.recall
    assert again.candidate_ids == live.candidate_ids
    assert again.api_error == live.api_error


def test_replay_is_byte_identical_across_runs(tmp_path, routes_dir, monkeypatch, capsys):
    """The property the whole harness rests on: same snapshot ⇒ same bytes.

    Two replays into one directory must match down to the byte in stdout and files.
    """
    routes = b.load_golden_routes(routes_dir)
    groups = [
        [
            run_of(
                r,
                api_response(
                    cover_points(r),
                    length_km=3.4,
                    walk_s=2400.0,
                ),
                repeat=i + 1,
            )
            for i in range(2)
        ]
        for r in routes
    ]
    snap = tmp_path / "snap"
    write_snapshot(snap, groups, repeat=2)
    out = tmp_path / "report"

    stdout1 = run_cli(["--replay", str(snap), "--report-dir", str(out)], monkeypatch, capsys)
    first = {p.name: p.read_bytes() for p in sorted(out.iterdir())}
    stdout2 = run_cli(["--replay", str(snap), "--report-dir", str(out)], monkeypatch, capsys)
    second = {p.name: p.read_bytes() for p in sorted(out.iterdir())}

    assert stdout1 == stdout2
    assert set(first) == {"report.json", "report.md", "report.metrics.jsonl"}
    assert first == second
    assert all(first.values())


def test_replay_into_two_separate_directories_matches_file_for_file(
    tmp_path, routes_dir, monkeypatch, capsys
):
    routes = b.load_golden_routes(routes_dir)
    write_snapshot(
        tmp_path / "snap",
        [
            [run_of(r, api_response(cover_points(r)), repeat=i + 1) for i in range(2)]
            for r in routes
        ],
        repeat=2,
    )
    run_cli(
        ["--replay", str(tmp_path / "snap"), "--report-dir", str(tmp_path / "a")],
        monkeypatch,
        capsys,
    )
    run_cli(
        ["--replay", str(tmp_path / "snap"), "--report-dir", str(tmp_path / "b")],
        monkeypatch,
        capsys,
    )
    for name in ("report.json", "report.md", "report.metrics.jsonl"):
        a = (tmp_path / "a" / name).read_bytes()
        b_ = (tmp_path / "b" / name).read_bytes()
        assert a == b_, f"{name} depends on the output directory"


def test_replay_reports_drift_when_the_metric_code_moved(tmp_path, routes_dir):
    """A recorded metric that no longer recomputes is reported, not hidden."""
    routes = b.load_golden_routes(routes_dir)
    golden = routes[0]
    path = write_snapshot(
        tmp_path / "snap",
        [[run_of(golden, api_response([point("a", 0.0, 1), point("b", 1.0, 2)]))]],
    )
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    lines[1]["metrics"]["recall_at_k"] = 0.5
    path.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8"
    )

    _groups, _meta, drift = b.run_replay(tmp_path / "snap")
    assert any("differ from the snapshot" in d["note"] for d in drift)


def test_replay_flags_a_reference_file_that_changed(tmp_path, routes_dir):
    routes = b.load_golden_routes(routes_dir)
    write_snapshot(
        tmp_path / "snap",
        [[run_of(routes[0], api_response([point("a", 0.0, 1), point("b", 1.0, 2)]))]],
    )
    payload = json.loads((routes_dir / "alpha.json").read_text(encoding="utf-8"))
    payload["stops"][0]["name"] = "A renamed"
    (routes_dir / "alpha.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    b._GOLDEN_SHA_CACHE.clear()

    _groups, _meta, drift = b.run_replay(tmp_path / "snap")
    assert any("golden reference .json changed" in d["note"] for d in drift)


def test_replay_fails_loudly_when_a_case_disappeared(tmp_path, routes_dir, capsys):
    routes = b.load_golden_routes(routes_dir)
    write_snapshot(
        tmp_path / "snap",
        [[run_of(routes[0], api_response([point("a", 0.0, 1), point("b", 1.0, 2)]))]],
    )
    (routes_dir / "alpha.json").unlink()
    with pytest.raises(SystemExit) as exc:
        b.run_replay(tmp_path / "snap")
    assert "alpha.json" in str(exc.value)


def test_replay_of_a_dir_without_a_snapshot_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        b.load_snapshot(tmp_path)


def test_a_replay_written_into_its_own_snapshot_dir_does_not_poison_the_next_one(
    tmp_path, routes_dir, monkeypatch, capsys
):
    """Regression: the default replay output dir IS the snapshot dir.

    A globbing loader would feed the first report back in as the second run's input.
    """
    routes = b.load_golden_routes(routes_dir)
    snap = tmp_path / "snap"
    write_snapshot(
        snap,
        [
            [run_of(r, api_response(cover_points(r)), repeat=i + 1) for i in range(2)]
            for r in routes
        ],
        repeat=2,
    )

    stdout1 = run_cli(["--replay", str(snap)], monkeypatch, capsys)
    first = {p.name: p.read_bytes() for p in sorted(snap.iterdir())}
    assert (snap / "report.metrics.jsonl").exists()
    assert (snap / "rows.jsonl").read_bytes() == first["rows.jsonl"]

    stdout2 = run_cli(["--replay", str(snap)], monkeypatch, capsys)
    second = {p.name: p.read_bytes() for p in sorted(snap.iterdir())}

    assert stdout1 == stdout2
    assert first == second
    assert "4 row(s), 2 case(s)" in stdout2


def test_percentile_is_nearest_rank_and_clamped():
    vals = [float(i) for i in range(101)]
    assert b.percentile(vals, 0.0) == 0.0
    assert b.percentile(vals, 1.0) == 100.0
    assert b.percentile(vals, 0.5) == 50.0
    assert b.percentile([], 0.5) != b.percentile([], 0.5)


def test_bootstrap_ci_on_hand_checkable_inputs():
    idx = b.resample_indices(2, 2000, 1)
    stats = b.bootstrap_ci([0.0, 1.0], idx)
    assert stats["mean"] == pytest.approx(0.5)
    assert stats["lo"] == pytest.approx(0.0)
    assert stats["hi"] == pytest.approx(1.0)
    assert stats["n"] == 2
    flat = b.bootstrap_ci([0.7, 0.7, 0.7], b.resample_indices(3, 500, 1))
    assert (flat["lo"], flat["hi"]) == (pytest.approx(0.7), pytest.approx(0.7))
    single = b.bootstrap_ci([0.4], b.resample_indices(1, 10, 1))
    assert (single["lo"], single["hi"], single["n"]) == (0.4, 0.4, 1)
    assert b.bootstrap_ci([], idx)["mean"] is None


def test_bootstrap_is_reproducible_from_its_seed():
    a = b.resample_indices(5, 1000, 7)
    b_ = b.resample_indices(5, 1000, 7)
    c = b.resample_indices(5, 1000, 8)
    assert a == b_
    assert a != c
    values = [0.1, 0.9, 0.4, 0.55, 0.2]
    assert b.bootstrap_ci(values, a) == b.bootstrap_ci(values, b_)


def test_default_bootstrap_is_ten_thousand_samples():
    assert b.BOOTSTRAP_SAMPLES == 10_000
    assert b.BOOTSTRAP_ALPHA == 0.05
    assert b.BOOTSTRAP_SEED == 20260926


def test_overall_stats_and_noise_floor_use_cases_not_runs(routes_dir):
    routes = b.load_golden_routes(routes_dir)
    groups = []
    for r in routes:
        groups.append(
            [
                run_of(r, api_response(cover_points(r)), repeat=1),
                run_of(r, api_response([point("a", 50.0)]), repeat=2),
            ]
        )
    per_case = b.per_case_scores(groups)
    assert set(per_case) == {"alpha", "beta"}
    assert per_case["alpha"]["recall_at_k"] == pytest.approx(0.5)
    assert per_case["beta"]["recall_at_k"] == pytest.approx(0.5)

    overall = b.overall_stats(per_case, samples=2000)
    assert overall["recall_at_k"]["n"] == 2
    assert overall["recall_at_k"]["mean"] == pytest.approx(0.5)
    assert overall["recall_at_k"]["lo"] == pytest.approx(0.5)
    assert overall["recall_at_k"]["hi"] == pytest.approx(0.5)

    noise = b.noise_floor(groups)
    assert noise["recall_at_k"]["mean_within_case_spread"] == pytest.approx(0.5)
    assert noise["recall_at_k"]["mean_within_case_std"] == pytest.approx(1 / math.sqrt(2))
    assert noise["recall_at_k"]["n_cases_with_repeats"] == 2
    single = b.noise_floor([[g[0]] for g in groups])
    assert single["recall_at_k"]["mean_within_case_spread"] is None


def test_noise_floor_matches_the_reference_measurement_it_is_compared_to():
    assert "0.711" in b.REFERENCE_NOISE_FLOOR
    assert "0.150" in b.REFERENCE_NOISE_FLOOR


def test_paired_bootstrap_on_identical_snapshots_is_exactly_zero():
    vals = [0.2, 0.5, 0.9]
    idx = b.resample_indices(3, 2000, 3)
    stats = b.paired_bootstrap(vals, vals, idx)
    assert stats["diff"] == pytest.approx(0.0)
    assert (stats["lo"], stats["hi"]) == (pytest.approx(0.0), pytest.approx(0.0))
    assert stats["p"] == pytest.approx(1.0)
    assert stats["n"] == 3


def test_paired_bootstrap_on_a_uniform_shift_is_maximally_significant():
    """Every case moved by exactly +0.2: the CI cannot straddle 0."""
    a = [0.2, 0.5, 0.9]
    b_ = [0.0, 0.3, 0.7]
    idx = b.resample_indices(3, 2000, 3)
    stats = b.paired_bootstrap(a, b_, idx)
    assert stats["diff"] == pytest.approx(0.2)
    assert (stats["lo"], stats["hi"]) == (pytest.approx(0.2), pytest.approx(0.2))
    assert stats["p"] == pytest.approx(1 / 2000)


def test_paired_bootstrap_cannot_distinguish_a_delta_below_the_noise_floor():
    """The ±0.15 the pipeline is known to wobble by: not evidence."""
    a = [0.80, 0.55, 0.70]
    b_ = [0.74, 0.61, 0.66]
    idx = b.resample_indices(3, 2000, 3)
    stats = b.paired_bootstrap(a, b_, idx)
    assert abs(stats["diff"]) < 0.15
    assert stats["lo"] <= 0.0 <= stats["hi"]
    assert stats["p"] > 0.05


def test_paired_bootstrap_edges():
    assert b.paired_bootstrap([], [], [])["diff"] is None
    assert b.paired_bootstrap([1.0], [0.5, 0.5], b.resample_indices(2, 10, 1))["diff"] is None
    one = b.paired_bootstrap([1.0], [0.5], b.resample_indices(1, 10, 1))
    assert one["diff"] == pytest.approx(0.5)
    assert one["n"] == 1
    assert one["lo"] is None and one["hi"] is None and one["p"] is None


def _snapshot_with(
    tmp_path: Path, name: str, per_case_recalls: dict[str, list[float]], routes_dir: Path
) -> Path:
    """A snapshot dir whose cases actually reach the requested recall values.

    recall 1.0 → the route stops on every reference stop; 0.0 → 50 km away.
    """
    goldens = {r.case: r for r in b.load_golden_routes(routes_dir)}
    groups = []
    for case, recalls in per_case_recalls.items():
        golden = goldens[case]
        runs = []
        for i, recall in enumerate(recalls):
            points = cover_points(golden) if recall > 0 else [point("far", 50.0, 1)]
            runs.append(run_of(golden, api_response(points), repeat=i + 1))
        groups.append(runs)
    d = tmp_path / name
    write_snapshot(d, groups, repeat=len(next(iter(per_case_recalls.values()))))
    return d


def test_compare_reports_a_paired_difference_with_a_p_value(tmp_path, routes_dir):
    good = _snapshot_with(tmp_path, "good", {"alpha": [1.0, 1.0], "beta": [1.0, 1.0]}, routes_dir)
    bad = _snapshot_with(tmp_path, "bad", {"alpha": [0.0, 0.0], "beta": [0.0, 0.0]}, routes_dir)
    cmp = b.compare_snapshots(good, bad, samples=2000, seed=5)

    assert cmp["shared_cases"] == ["alpha", "beta"]
    row = next(r for r in cmp["metrics"] if r["label"] == "Rec@K")
    assert row["mean_a"] == pytest.approx(1.0)
    assert row["mean_b"] == pytest.approx(0.0)
    assert row["diff"] == pytest.approx(1.0)
    assert row["lo"] > 0.0
    assert row["p"] <= 1 / 2000
    assert row["exceeds_noise_floor"] is True
    assert cmp["a"]["n_hard_failure_runs"] == 0
    assert cmp["a"]["n_leg_sanity_defects"] == 0


def test_compare_of_a_snapshot_with_itself_is_exactly_null(tmp_path, routes_dir):
    snap = _snapshot_with(tmp_path, "a", {"alpha": [1.0, 0.5], "beta": [0.0, 1.0]}, routes_dir)
    cmp = b.compare_snapshots(snap, snap, samples=1000, seed=5)
    seen_null_ci = False
    for row in cmp["metrics"]:
        if row["diff"] is None:
            continue
        assert row["diff"] == pytest.approx(0.0)
        if row["n_paired"] < 2:
            assert row["p"] is None and row["lo"] is None
            seen_null_ci = True
        else:
            assert row["p"] == pytest.approx(1.0)
    assert seen_null_ci


def test_compare_pairs_only_the_cases_both_snapshots_cover(tmp_path, routes_dir):
    a = _snapshot_with(tmp_path, "a", {"alpha": [1.0], "beta": [0.5]}, routes_dir)
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "rows.jsonl").write_text(
        "\n".join(
            line
            for line in (a / "rows.jsonl").read_text(encoding="utf-8").splitlines()
            if '"case": "beta"' not in line
        )
        + "\n",
        encoding="utf-8",
    )
    cmp = b.compare_snapshots(a, bdir, samples=500, seed=5)
    assert cmp["shared_cases"] == ["alpha"]
    assert cmp["only_in_a"] == ["beta"]
    assert cmp["only_in_b"] == []


def test_compare_cli_writes_a_report(tmp_path, routes_dir, monkeypatch, capsys):
    a = _snapshot_with(tmp_path, "a", {"alpha": [1.0], "beta": [1.0]}, routes_dir)
    bdir = _snapshot_with(tmp_path, "b", {"alpha": [0.0], "beta": [0.0]}, routes_dir)
    out = run_cli(
        [
            "--compare",
            str(a),
            str(bdir),
            "--report-dir",
            str(tmp_path / "out"),
            "--bootstrap",
            "500",
        ],
        monkeypatch,
        capsys,
    )
    assert "PAIRED BOOTSTRAP" in out
    assert "Rec@K" in out
    saved = json.loads((tmp_path / "out" / "compare.json").read_text(encoding="utf-8"))
    assert saved["bootstrap"]["paired"] is True
    assert len(saved["metrics"]) == len(b.COMPARE_METRICS)


def test_report_states_that_the_pool_is_a_proxy_and_keeps_failures_outside(
    tmp_path, routes_dir, monkeypatch, capsys
):
    routes = b.load_golden_routes(routes_dir)
    groups = [
        [
            run_of(
                routes[0],
                api_response(
                    [
                        {
                            "name": DUP_NAME_A,
                            "lat": DUP_LAT_A,
                            "lon": DUP_LON_A,
                            "visit_minutes": 20,
                        },
                        {
                            "name": DUP_NAME_B,
                            "lat": DUP_LAT_B,
                            "lon": DUP_LON_B,
                            "visit_minutes": 35,
                        },
                    ]
                ),
            )
        ],
        [
            b.RunRecord(
                case="beta",
                case_name="Beta walk",
                repeat=1,
                result=b.score_response(routes[1], None, http_status=500, api_error="boom"),
            )
        ],
    ]
    snap = tmp_path / "snap"
    write_snapshot(snap, groups)
    out = run_cli(
        ["--replay", str(snap), "--report-dir", str(tmp_path / "out")], monkeypatch, capsys
    )

    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    notes = report["metric_notes"]
    assert "does not expose the pre-rerank candidate pool" in notes["stage_split"]
    assert "upper bound on pool recall" in notes["stage_split"]
    assert "gated kinds" in notes["hard_failures"]
    assert report["hard_failures"]["n_failed_runs"] == 1
    assert report["hard_failures"]["leg_sanity_defects"] == 1
    alpha = next(r for r in report["results"] if r["case"] == "alpha")
    assert alpha["leg_sanity"]["n_duplicate_stops"] == 1
    assert alpha["runs"][0]["hard_failure"] is False
    assert "HARD FAILURES (not part of any score above)" in out
    assert "leg-sanity defects kept in the means" in out
    assert b.REFERENCE_NOISE_FLOOR in (tmp_path / "out" / "report.md").read_text(encoding="utf-8")


def test_metrics_jsonl_is_one_line_per_case_plus_an_overall_line(
    tmp_path, routes_dir, monkeypatch, capsys
):
    routes = b.load_golden_routes(routes_dir)
    groups = [[run_of(r, api_response(cover_points(r)))] for r in routes]
    snap = tmp_path / "snap"
    write_snapshot(snap, groups)
    run_cli(["--replay", str(snap), "--report-dir", str(tmp_path / "out")], monkeypatch, capsys)

    lines = [
        json.loads(x)
        for x in (tmp_path / "out" / "report.metrics.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [line["case"] for line in lines] == ["alpha", "beta", "__overall__"]
    assert lines[0]["scores"]["recall_at_k"] == pytest.approx(1.0)
    assert "mean_and_ci95" in lines[-1]
    assert lines[-1]["n_hard_failure_runs"] == 0


def test_strict_turns_a_hard_failure_into_a_nonzero_exit(tmp_path, routes_dir, monkeypatch, capsys):
    routes = b.load_golden_routes(routes_dir)
    groups = [
        [
            b.RunRecord(
                case="alpha",
                case_name="Alpha walk",
                repeat=1,
                result=b.score_response(routes[0], None, http_status=422, api_error="boom"),
            )
        ]
    ]
    snap = tmp_path / "snap"
    write_snapshot(snap, groups)
    argv = ["bench_routes.py", "--replay", str(snap), "--report-dir", str(tmp_path / "o")]

    monkeypatch.setattr(sys, "argv", argv)
    b.main()
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", [*argv, "--strict"])
    with pytest.raises(SystemExit) as exc:
        b.main()
    assert exc.value.code == 1
    capsys.readouterr()


def test_case_filter_rejects_an_unknown_case(routes_dir, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["bench_routes.py", "--case", "nope"])
    with pytest.raises(SystemExit):
        b.main()
    capsys.readouterr()


def test_reference_walk_is_unchanged_by_the_harness_rewrite():
    """The corrected shared-subset tau + Held-Karp reference must not regress."""
    ref = b.build_reference_walk(golden_stops(0.0, 10.0, 1.0, 11.0))
    assert ref.order == [0, 2, 1, 3]
    assert ref.distance_km == pytest.approx(11.0, abs=1e-6)
    assert b.kendall_tau([4, 7, 9], [4, 7, 9]) == pytest.approx(1.0)
    assert b.kendall_tau([4, 7], [4, 7]) is None


def test_evaluate_still_works_through_call_generate(monkeypatch):
    """The live single-run entry point keeps its old monkeypatch surface."""
    golden = make_golden()
    monkeypatch.setattr(b, "call_generate", lambda *a, **k: api_response(cover_points(golden)))
    result = b.evaluate(golden, "http://unused")
    assert result.api_error is None
    assert result.recall_at_k == pytest.approx(1.0)
    assert result.stage.recall == pytest.approx(1.0)

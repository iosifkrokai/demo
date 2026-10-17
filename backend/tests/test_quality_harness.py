"""The harness's own parts: what it records, what it reads, whose codes it asks for."""

from __future__ import annotations

import json
import os
from typing import Any

from agent.tools.places import Where, find_places
from agent.tools.plan import plan_walk
from db.seed import cli, datasets as ds_mod, itineraries as itin_mod
from quality import evals as evals_mod, report as report_mod
from quality.bench.analysis import (
    _leg_failures,
    duplicate_exceptions,
    find_duplicate_stop_pairs,
    leg_cap_km,
    leg_sanity,
    norm_name,
)
from quality.bench.cases import _check_curated_case, load_curated_routes
from quality.bench.cli import _report_dir
from quality.bench.config import BENCH_CURATED, BENCH_CURATED_OUT
from quality.bench.models import OurStop
from quality.evals import _forbidden, _load, _service_checks, report as evals_report
from quality.report import read_curated, read_routes
from quality.trajectory import _record_args
from routing.models import WalkRequest


def test_a_tool_taking_a_request_is_recorded_as_its_fields() -> None:
    """The agent passed `budget_minutes`, not a `WalkRequest`: an envelope of ours
    must not become the thing a case has to name."""
    request = WalkRequest(
        stop_ids=[11, 12],
        profile="pedestrian",
        budget_minutes=120,
        origin=None,
        round_trip=False,
        must_keep_ids=None,
        locale="ru",
    )

    args = _record_args(plan_walk, (request,), {"repos": object(), "facts": None})

    assert args["budget_minutes"] == 120
    assert args["stop_ids"] == [11, 12]
    assert args["profile"] == "pedestrian"
    assert "repos" not in args
    assert "facts" not in args


def test_a_nested_model_is_recorded_as_a_mapping() -> None:
    """`where` is a model a case describes as JSON, so it is compared as one."""
    args = _record_args(
        find_places,
        ("старый город",),
        {"where": Where(area="grodno-old-town"), "repos": None},
    )

    assert args["where"]["area"] == "grodno-old-town"


def _routes_report_body() -> str:
    return json.dumps({"overall": {"recall_at_k": {"mean": 0.5}}, "n_runs": 1})


def test_a_live_routes_run_is_found_next_to_a_replay(monkeypatch, tmp_path) -> None:
    """A live run writes to `reports/`, a replay into its dated snapshot directory."""
    reports = tmp_path / "reports"
    snapshot = reports / "snapshots" / "2026-01-01"
    snapshot.mkdir(parents=True)
    replayed = snapshot / "report.json"
    replayed.write_text(_routes_report_body(), encoding="utf-8")
    os.utime(replayed, (0, 0))
    live = reports / "report.json"
    live.write_text(_routes_report_body(), encoding="utf-8")

    monkeypatch.setattr(report_mod, "REPORTS", reports)
    monkeypatch.setattr(report_mod, "SNAPSHOTS", reports / "snapshots")

    got = read_routes()

    assert got["measured"] is True
    assert got["source"].endswith("reports/report.json")


def test_a_replayed_routes_run_is_still_found(monkeypatch, tmp_path) -> None:
    reports = tmp_path / "reports"
    snapshot = reports / "snapshots" / "2026-01-01"
    snapshot.mkdir(parents=True)
    (snapshot / "report.json").write_text(_routes_report_body(), encoding="utf-8")

    monkeypatch.setattr(report_mod, "REPORTS", reports)
    monkeypatch.setattr(report_mod, "SNAPSHOTS", reports / "snapshots")

    got = read_routes()

    assert got["measured"] is True
    assert got["source"].endswith("snapshots/2026-01-01/report.json")


def test_one_object_twice_in_a_metre_is_still_a_duplicate() -> None:
    """The rule keeps its teeth: two stops of the same name a metre apart, not in
    the exception list, are still `coincident`/`same_name` and still count."""
    stops = [
        OurStop(name="Мирский замок", lat=53.4517, lon=26.4736, place_id=1),
        OurStop(name="Мирский замок", lat=53.45171, lon=26.4736, place_id=2),
    ]

    pairs = find_duplicate_stop_pairs(stops)

    assert len(pairs) == 1
    assert pairs[0]["rule"] in {"coincident", "same_name"}
    assert pairs[0]["excluded"] is False
    assert leg_sanity(stops, {}, {}).n_duplicate_stops == 1


def test_a_declared_pair_of_neighbours_is_not_a_duplicate() -> None:
    """A pair named in the exception file is still reported, with the rule it would
    have fired, but it is `excluded` and raises no `duplicate_stop`."""
    stops = [
        OurStop(
            name="Фарный костёл Святого Франциска Ксаверия",
            lat=53.6787,
            lon=23.8279,
            place_id=4,
        ),
        OurStop(name="Музей истории города Гродно", lat=53.6783, lon=23.8265, place_id=12),
    ]

    pairs = find_duplicate_stop_pairs(stops)

    assert len(pairs) == 1
    assert pairs[0]["rule"] == "coincident"
    assert pairs[0]["excluded"] is True
    assert pairs[0]["exception_reason"]

    leg = leg_sanity(stops, {}, {})
    assert leg.n_duplicate_stops == 0
    assert leg.n_legitimate_adjacent == 1
    assert "duplicate_stop" not in [f.kind for f in _leg_failures(leg)]


def test_a_real_duplicate_still_fails_beside_an_excluded_pair() -> None:
    """An exception covers one pair only: a genuine twin in the same route is still
    counted, and the excluded pair does not hide it."""
    stops = [
        OurStop(
            name="Фарный костёл Святого Франциска Ксаверия",
            lat=53.6787,
            lon=23.8279,
            place_id=4,
        ),
        OurStop(name="Музей истории города Гродно", lat=53.6783, lon=23.8265, place_id=12),
        OurStop(name="Мирский замок", lat=53.4517, lon=26.4736, place_id=56),
        OurStop(name="Мирский замок", lat=53.45171, lon=26.4736, place_id=57),
    ]

    leg = leg_sanity(stops, {}, {})

    assert leg.n_duplicate_stops == 1
    assert leg.n_legitimate_adjacent == 1
    kinds = [f.kind for f in _leg_failures(leg)]
    assert kinds.count("duplicate_stop") == 1


def test_the_exception_list_parses_and_names_distinct_pairs() -> None:
    """The list is a reviewable file: every entry is two distinct normalised names.

    The count is pinned so a new pair cannot be declared unnoticed."""
    exceptions = duplicate_exceptions()

    assert len(exceptions) == 17
    for entry in exceptions.values():
        assert entry["reason"].strip()
        assert entry["source_urls"]
        assert len({norm_name(n) for n in entry["names"]}) == 2


def test_the_services_stage_asks_for_the_taxonomy_the_codes_come_from(db_repos) -> None:
    """An empty answer must mean "nothing is beside this line", not "the harness
    forgot to hand in the service codes the API hands in"."""
    codes = tuple(db_repos.taxonomy.service_codes())
    case = next(c for c in _load("services_along") if c.id == "centre_sovetskaya_walk")

    checks, _ties = _service_checks(db_repos.places, case, codes)

    membership = next(c for c in checks if c["check"] == "membership_agrees")
    assert membership["ok"] is True, membership["detail"]


def test_a_ready_made_walk_is_measured_without_copying_its_stops() -> None:
    """A curated case is a sidecar plus the itinerary: the stops come from the
    datasets the seed projects, so a walk and its benchmark cannot disagree."""
    routes = load_curated_routes()
    walks = {walk["id"]: walk for walk in itin_mod.load_itineraries()}
    collected, _shadowed, _stale = cli._collect(ds_mod.default_datasets())
    places = {r["source_url"]: r for r in collected["records"]}

    assert routes, "the curated set is empty"
    for route in routes:
        walk = walks[route.case]
        assert route.profile == walk["transport"]
        assert route.budget_minutes > 0
        assert route.origin == (route.stops[0].lat, route.stops[0].lon)
        for stop, key in zip(route.stops, walk["stops"], strict=True):
            assert (stop.lat, stop.lon) == (places[key]["lat"], places[key]["lon"])


def test_a_sidecar_carrying_what_nothing_reads_is_refused(tmp_path) -> None:
    """The file holds the query and the budget; a field the loader ignores would
    sit there looking like it did something."""
    case = tmp_path / "old-town-castles.json"
    good = {"id": "old-town-castles", "query_ru": "Гродно", "budget_minutes": 120}
    assert _check_curated_case(good, case) == []

    assert _check_curated_case({**good, "note": "ok", "profile": "auto"}, case) == [
        f"{case}: unknown key 'profile'"
    ]
    assert _check_curated_case({**good, "id": "other"}, case) == [
        f"{case}: id must be 'old-town-castles', not 'other'"
    ]
    assert _check_curated_case({**good, "budget_minutes": 600}, case) == [
        f"{case}: budget_minutes must be an int in 1..480"
    ]
    assert _check_curated_case({k: v for k, v in good.items() if k != "query_ru"}, case) == [
        f"{case}: missing key 'query_ru'"
    ]


def test_a_curated_run_is_filed_under_the_cases_it_measured(monkeypatch, tmp_path) -> None:
    """A report names its own set: a curated run must not overwrite the benchmark
    walks' numbers under the benchmark walks' name."""

    class Args:
        report_dir = None

    assert _report_dir(Args(), tmp_path, ["old-town-castles"]) == BENCH_CURATED_OUT
    assert _report_dir(Args(), tmp_path, ["grodno_old_town"]) == tmp_path
    assert BENCH_CURATED.name == "curated"


def test_the_leg_cap_follows_the_transport() -> None:
    """5 km on foot is a defect by bicycle and no defect at all by car."""
    stops = [
        OurStop(name="Лида", lat=53.8872, lon=25.3028),
        OurStop(name="Крево", lat=54.3093, lon=26.2825),
    ]

    assert leg_cap_km("pedestrian") < leg_cap_km("bicycle") < leg_cap_km("auto")
    assert leg_sanity(stops, {}, {}, "pedestrian").over_cap is True
    assert leg_sanity(stops, {}, {}, "bicycle").over_cap is True
    assert leg_sanity(stops, {}, {}, "auto").over_cap is False
    assert leg_sanity(stops, {}, {}, "auto").max_leg_km_straight > 45.0


def test_the_leg_cap_is_the_budget_the_engine_was_given() -> None:
    """The engine bounds a leg by a share of the request's own time, so the harness
    must too: half a day on foot allows less than five kilometres, and a day's drive
    allows the whole day rather than the four-hour fallback."""
    assert leg_cap_km("pedestrian", 120) < leg_cap_km("pedestrian")
    assert round(leg_cap_km("bicycle", 480), 6) == 20.0
    assert round(leg_cap_km("auto", 480), 6) == 360.0
    assert leg_cap_km("auto", 480) > leg_cap_km("auto")

    stops = [
        OurStop(name="Гродно", lat=53.6778, lon=23.8295),
        OurStop(name="Слоним", lat=53.0936, lon=25.3177),
    ]
    assert leg_sanity(stops, {}, {}, "auto", 60).over_cap is True
    assert leg_sanity(stops, {}, {}, "auto", 480).over_cap is False


def test_the_curated_row_reads_the_set_its_report_names(monkeypatch, tmp_path) -> None:
    """A report carries `cases_dir`, so the one-pager cannot label a curated run as
    the benchmark walks — or the other way round."""
    reports = tmp_path / "reports"
    (reports / "curated").mkdir(parents=True)
    body = {"overall": {"recall_at_k": {"mean": 0.5}}, "n_runs": 1}
    curated = reports / "curated" / "report.json"
    curated.write_text(json.dumps({**body, "cases_dir": "quality/cases/curated"}))
    routes = reports / "report.json"
    routes.write_text(json.dumps({**body, "cases_dir": "quality/cases/routes"}))

    monkeypatch.setattr(report_mod, "REPORTS", reports)
    monkeypatch.setattr(report_mod, "SNAPSHOTS", reports / "snapshots")
    monkeypatch.setattr(report_mod, "CURATED", curated)

    assert read_routes()["layer"] == "quality/cases/routes"
    assert read_routes()["source"].endswith("reports/report.json")
    assert read_curated()["layer"] == "quality/cases/curated"
    assert read_curated()["measured"] is True


def _passing_check() -> dict[str, Any]:
    return {
        "case": "a",
        "check": "verdict",
        "ok": True,
        "known_gap": False,
        "detail": "",
        "why": "",
    }


def test_a_stage_that_did_not_run_is_named_and_left_out_of_the_total() -> None:
    """The live-model stage is opt-in: a green total has to say what it left out."""
    text = evals_report(
        [
            {"stage": "verdicts", "checks": [_passing_check()]},
            {"stage": "plan", "checks": [_passing_check()]},
        ]
    )

    assert "НЕ ИЗМЕРЯЛОСЬ (2, вес 0.50 — в итог не входит)" in text
    assert "веса учтённых участков: verdicts 0.25, plan 0.25" in text
    assert "добавь `--stage interpretation`" in text


def test_a_reading_a_case_rules_out_is_caught_by_kind_strength_and_code() -> None:
    """A case can say what the words must not be taken to mean."""
    any_service = [{"kind": "service", "code": None, "strength": "hard"}]
    the_cafe = [{"kind": "service", "code": "кафе", "strength": "hard"}]

    assert _forbidden([("service", "кафе", "hard")], any_service) == ["service/кафе/hard"]
    assert _forbidden([("service", "кафе", "soft")], any_service) == []
    assert _forbidden([("interest", "кафе", "hard")], any_service) == []
    assert _forbidden([("service", "музей", "hard")], the_cafe) == []
    assert _forbidden([("service", "кафе", "hard")], the_cafe) == ["service/кафе/hard"]


def test_a_skipped_stage_is_named_with_its_reason_and_weighs_nothing() -> None:
    text = evals_report(
        [
            {"stage": "verdicts", "checks": [_passing_check()]},
            {"stage": "services", "checks": [], "skipped": "нет базы"},
            {"stage": "plan", "checks": [_passing_check()]},
        ]
    )

    assert "services — нет базы" in text
    assert "веса учтённых участков: verdicts 0.25, plan 0.25" in text
    assert "НЕ ИЗМЕРЯЛОСЬ (2, вес 0.50 — в итог не входит)" in text


class _Stop:
    """A reference stop, as the retrieval stage reads it off a case."""

    def __init__(self, lat: float, lon: float) -> None:
        self.lat = lat
        self.lon = lon


class _Row:
    def __init__(self, place_id: int, name: str) -> None:
        self.id = place_id
        self.name = name


class _Places:
    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows

    def keyword_search(self, query: str, limit: int = 20) -> list[_Row]:
        return self.rows


class _Repos:
    def __init__(self, places: _Places) -> None:
        self.places = places


def test_a_stop_the_corpus_names_differently_is_a_case_gap() -> None:
    """The cases are Wikivoyage walks: their name for a place is not ours, and a stop
    we hold under another name says nothing about the search."""
    repos = _Repos(_Places([_Row(31, "Фарный костёл Святого Франциска Ксаверия")]))

    assert evals_mod._held(repos, "Кафедральный костёл Святого Франциска Ксаверия") is None
    assert evals_mod._held(repos, "Фарный костёл Святого Франциска Ксаверия").id == 31


def test_the_vector_search_is_provisioned_for_the_pool_it_serves() -> None:
    """`ef_search` under the pool width drops true neighbours — measured: the exact row
    of «Монастырь бригиток» is rank 1 at 400 and absent from the pool at 40."""
    from core import constants
    from db.connection import EF_SEARCH

    assert EF_SEARCH >= constants.RETRIEVAL_POOL_SIZE


def test_the_pool_is_measured_with_the_benchmarks_own_radius() -> None:
    """The retrieval stage must not invent a second criterion: it reads the runner's."""
    from quality.bench.config import STAGE1_PROXY_RADIUS_M

    stop = _Stop(53.6778, 23.8295)
    just_inside = [{"lat": 53.6778 + 0.002, "lon": 23.8295}]
    just_outside = [{"lat": 53.6778 + 0.003, "lon": 23.8295}]

    assert STAGE1_PROXY_RADIUS_M == 250.0
    assert evals_mod._reaches(stop, just_inside) is True
    assert evals_mod._reaches(stop, just_outside) is False
    assert evals_mod._nearest_km(stop, []) is None


def test_the_pool_recall_counts_stops_not_rows() -> None:
    stops = [_Stop(53.6778, 23.8295), _Stop(53.6791, 23.8216)]
    near_the_first = [{"lat": 53.6778, "lon": 23.8295}, {"lat": 53.6778, "lon": 23.8295}]

    assert evals_mod._covered(stops, near_the_first) == 1
    assert evals_mod._covered(stops, []) == 0


def test_the_retrieval_stage_does_not_enter_the_weighted_total() -> None:
    """It reports a number, so it must not re-weight the four stages it sits beside."""
    text = evals_report(
        [
            {"stage": "verdicts", "checks": [_passing_check()]},
            {"stage": "retrieval", "checks": [_passing_check()]},
            {"stage": "plan", "checks": [_passing_check()]},
        ]
    )

    assert "веса учтённых участков: verdicts 0.25, plan 0.25" in text
    assert "retrieval" in text


def test_the_pool_recall_is_printed_under_its_own_row() -> None:
    text = evals_report(
        [
            {
                "stage": "retrieval",
                "checks": [_passing_check()],
                "pool_recall": {"whole": 0.417, "per_object": 0.806, "stops": 36},
            }
        ]
    )

    assert "резерв пула (250 м, 36 остановок): одним запросом 42%, по объектам 81%" in text

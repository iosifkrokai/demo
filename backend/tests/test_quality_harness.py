"""The harness's own parts: what it records, what it reads, whose codes it asks for."""

from __future__ import annotations

import json
import os

from agent.tools.places import Where, find_places
from agent.tools.plan import plan_walk
from quality import report as report_mod
from quality.evals import _load, _service_checks
from quality.report import read_routes
from quality.runner import (
    OurStop,
    _leg_failures,
    duplicate_exceptions,
    find_duplicate_stop_pairs,
    leg_sanity,
    norm_name,
)
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
    """The list is a reviewable file: every entry is two distinct normalised names."""
    exceptions = duplicate_exceptions()

    assert len(exceptions) == 6
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

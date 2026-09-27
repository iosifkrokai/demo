"""Golden-set compliance scorer for scripts/bench_routes.py.

No network, no DB, no agent: every plan is hand-built, so each expected verdict
is derivable by hand and the whole file runs offline in CI.

What is pinned here:

  * all thirteen committed golden cases satisfy the documented schema, their ids
    match their file names, and every category is a canonical domain code;
  * the RU/EN parity pairs really are ONE request in two languages — same
    filters, same expectations, one case per locale, each query in its own
    language — and a pair that is not gets rejected at load time;
  * the scorer ACCEPTS a good plan and rejects each failure mode with the right
    machine-readable reason: missing mandatory category, forbidden category,
    missing named place, out-of-region point, over-budget total, wrong status,
    empty plan;
  * a status the API cannot express is derived from the best available evidence
    and its source is recorded; a check the response cannot answer is reported
    as unverified rather than silently passed;
  * the aggregate compliance rate counts cases AND parity groups;
  * the script refuses to produce a report when the backend or Valhalla is not
    running (exit code 2, nothing written).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from scripts import bench_routes as b

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "golden"
ROUTES_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "routes"

# Real coordinates: Grodno old town is inside the project area, Vilnius is not.
GRODNO = (53.6778, 23.8295)
FARNYI = (53.6789, 23.8306)
VILNIUS = (54.6858, 25.2877)


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def make_case(
    case_id: str = "synthetic",
    *,
    locale: str = "ru",
    query: str = "прогулка по Гродно на два часа",
    filters: dict | None = None,
    expectations: dict | None = None,
    parity_group: str | None = None,
) -> b.GoldenCase:
    """A valid case by construction; pass filters/expectations to break one part."""
    flt = {
        "party_children": None,
        "hard_services": [],
        "interests": [],
        "avoid": [],
        "time_budget_minutes": 120,
        "origin": {"lat": GRODNO[0], "lon": GRODNO[1]},
        "result_mode": "route",
    }
    flt.update(filters or {})
    exp = {
        "must_contain_categories": [],
        "must_not_contain_categories": [],
        "expected_status": ["ready"],
        "max_total_minutes": 120,
        "in_region": True,
    }
    exp.update(expectations or {})
    return b.GoldenCase(
        id=case_id,
        locale=locale,
        query=query,
        filters=flt,
        expectations=exp,
        parity_group=parity_group,
    )


def case_dict(data: dict) -> dict:
    """A schema-valid raw case (the dict side of the same builder)."""
    return {
        "id": data.get("id", "synthetic"),
        "locale": data.get("locale", "ru"),
        "query": data.get("query", "прогулка по Гродно"),
        "filters": {
            "party_children": None, "hard_services": [], "interests": [], "avoid": [],
            "time_budget_minutes": 120,
            "origin": {"lat": GRODNO[0], "lon": GRODNO[1]},
            "result_mode": "route",
            **(data.get("filters") or {}),
        },
        "expectations": {
            "must_contain_categories": [], "must_not_contain_categories": [],
            "expected_status": ["ready"], "max_total_minutes": 120, "in_region": True,
            **(data.get("expectations") or {}),
        },
        **(data.get("extra") or {}),
    }


def point(
    name: str = "Остановка",
    *,
    latlon: tuple[float, float] = GRODNO,
    category: str | None = None,
    pid: int = 1,
    visit: int = 20,
) -> dict:
    out = {
        "id": pid, "name": name, "lat": latlon[0], "lon": latlon[1],
        "visit_minutes": visit,
    }
    if category is not None:
        out["category"] = category
    return out


def plan(
    points: list[dict],
    *,
    total_minutes: int = 100,
    walk_s: float = 2400.0,
    shape: dict | None = None,
    **extra,
) -> dict:
    """A /routes/generate response in the shape the API actually returns."""
    body = {
        "parsed": {"source": "regex"},
        "points": points,
        "shape": shape if shape is not None else {"type": "LineString", "coordinates": []},
        "summary": {"length_km": 3.0, "time_seconds": walk_s},
        "budget": {
            "budget_minutes": 120,
            "walk_minutes": int(walk_s / 60),
            "visit_minutes": sum(p.get("visit_minutes") or 0 for p in points),
            "total_minutes": total_minutes,
            "fits": total_minutes <= 120,
            "stops_dropped": 0,
        },
        "costing": "pedestrian",
        "debug": {"intent_source": "regex", "trace": {"algorithm": "2opt"}},
    }
    body.update(extra)
    return body


def verdict_of(case: b.GoldenCase, raw: dict | None, **kw) -> b.ComplianceVerdict:
    return b.evaluate_compliance(case, raw, **kw)


def run_cli(argv: list[str], monkeypatch, capsys) -> str:
    monkeypatch.setattr(sys, "argv", ["bench_routes.py", *argv])
    b.main()
    return capsys.readouterr().out


@pytest.fixture
def routes_dir() -> Path:
    """The committed reference walks: this file reads them, never rewrites them."""
    return ROUTES_DIR


@pytest.fixture
def golden_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point the golden loader at synthetic cases, never the committed ones."""
    d = tmp_path / "golden"
    d.mkdir()
    monkeypatch.setattr(b, "BENCH_GOLDEN", d)
    return d


def write_case(directory: Path, data: dict) -> Path:
    path = directory / f"{data['id']}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# the committed set: it parses, it validates, it covers the brief
# --------------------------------------------------------------------------


def test_the_committed_golden_set_parses_and_satisfies_the_schema():
    cases = b.load_golden_cases(GOLDEN_DIR)

    assert len(cases) >= 12, "the brief asks for at least 12 cases"
    assert len(cases) == 13
    for case in cases:
        assert case.path is not None
        assert case.id == case.path.stem, "id must equal the file name"
        assert case.locale in ("ru", "en")
        assert case.filters and case.expectations
    # validate_golden_case returning nothing for every file is the schema check;
    # load_golden_cases raises if any file breaks it, so reaching here is it.
    for case in cases:
        data = json.loads(case.path.read_text(encoding="utf-8"))
        assert b.validate_golden_case(data, case.path) == []


def test_every_category_in_the_committed_set_is_a_canonical_code():
    cases = b.load_golden_cases(GOLDEN_DIR)
    used = set()
    for case in cases:
        used |= set(case.filters["hard_services"])
        used |= set(case.filters["interests"])
        used |= set(case.filters["avoid"])
        used |= set(case.expectations["must_contain_categories"])
        used |= set(case.expectations["must_not_contain_categories"])
    assert used  # the set does state conditions
    assert used <= set(b.CANONICAL_CATEGORIES.values())
    # "зоопарк" is not a domain code: a case must not pretend it is one.
    assert "зоопарк" not in used


def test_the_committed_set_covers_every_scenario_the_brief_names():
    by_id = {c.id: c for c in b.load_golden_cases(GOLDEN_DIR)}
    assert len(by_id) == 13

    # a RU/EN parity pair
    assert by_id["family_walk_ru"].parity_group == by_id["family_walk_en"].parity_group
    # a mandatory toilet, a soft cafe, a two-hour budget
    hard = by_id["hard_toilet_soft_cafe_en"]
    assert hard.filters["hard_services"] == ["туалет"]
    assert "кафе" in hard.filters["interests"]
    assert hard.filters["time_budget_minutes"] == 120
    # a named place that must appear; a forbid request; an out-of-region request
    assert by_id["named_farnyi"].expectations["must_contain_names"] == ["Фарный"]
    assert by_id["named_mir_castle"].expectations["must_contain_names"] == ["Мир"]
    assert by_id["avoid_cafe_want_parks"].expectations["must_not_contain_categories"]
    assert by_id["avoid_temples"].filters["avoid"] == ["храм", "костёл", "церковь", "монастырь"]
    vilnius = by_id["out_of_region_vilnius"]
    assert vilnius.filters["origin"] is None
    assert vilnius.expectations["allow_empty"] is True
    assert vilnius.expectations["in_region"] is True
    # a region-wide catalogue, explicitly not a short walk
    churches = by_id["all_churches_catalogue"]
    assert churches.filters["result_mode"] == "catalogue"
    assert churches.expectations["expected_result_mode"] == "catalogue"
    assert churches.expectations["min_places"] > 3
    # a vague request with no anchor
    assert by_id["vague_no_anchor"].filters["origin"] is None
    assert by_id["vague_no_anchor"].filters["time_budget_minutes"] is None


def test_only_the_cases_that_mean_it_allow_an_empty_plan():
    by_id = {c.id: c for c in b.load_golden_cases(GOLDEN_DIR)}
    allowed = {cid for cid, c in by_id.items() if c.expectations.get("allow_empty")}
    # An empty plan is an honest outcome exactly for the request that must be
    # refused and for the one that must be clarified.
    assert allowed == {"out_of_region_vilnius", "vague_no_anchor"}


# --------------------------------------------------------------------------
# RU/EN parity: the files
# --------------------------------------------------------------------------


def test_the_ru_en_parity_pairs_really_express_the_same_request():
    cases = b.load_golden_cases(GOLDEN_DIR)
    groups = b.parity_groups(cases)
    assert set(groups) == {"family_walk", "museums_budget"}
    for name, members in groups.items():
        assert sorted(c.locale for c in members) == ["en", "ru"], name
        signatures = {b._parity_signature(c) for c in members}
        assert len(signatures) == 1, f"{name}: filters/expectations differ by locale"
        assert len({c.query for c in members}) == 2, f"{name}: same query twice"

    # the §9.1 acceptance request, verbatim in its two languages
    ru = next(c for c in groups["family_walk"] if c.locale == "ru")
    en = next(c for c in groups["family_walk"] if c.locale == "en")
    assert ru.filters == en.filters
    assert ru.filters["party_children"] == 2
    assert ru.filters["hard_services"] == ["туалет"]
    assert ru.expectations == en.expectations
    assert "туалет обязателен" in ru.query
    assert "toilet" in en.query.lower()


def test_parity_validation_rejects_a_pair_that_is_not_the_same_request(golden_dir):
    write_case(golden_dir, case_dict({"id": "walk_ru", "locale": "ru",
                                      "query": "прогулка по Гродно с детьми",
                                      "extra": {"parity_group": "walk"}}))
    # The EN twin states a different budget: not the same request.
    write_case(golden_dir, case_dict({
        "id": "walk_en", "locale": "en", "query": "a walk around Grodno with children",
        "filters": {"time_budget_minutes": 240},
        "extra": {"parity_group": "walk"},
    }))
    with pytest.raises(b.GoldenCaseError) as exc:
        b.load_golden_cases(golden_dir)
    assert "parity_group 'walk'" in str(exc.value)
    assert "not the same request" in str(exc.value)


def test_parity_validation_rejects_a_pair_missing_a_locale(golden_dir):
    write_case(golden_dir, case_dict({"id": "walk_ru", "locale": "ru",
                                      "query": "прогулка по Гродно",
                                      "extra": {"parity_group": "walk"}}))
    with pytest.raises(b.GoldenCaseError) as exc:
        b.load_golden_cases(golden_dir)
    assert "one case per locale" in str(exc.value)


# --------------------------------------------------------------------------
# the schema validator itself
# --------------------------------------------------------------------------


def test_the_validator_accepts_a_minimal_valid_case():
    data = case_dict({"id": "ok"})
    assert b.validate_golden_case(data, Path("ok.json")) == []
    case = b.golden_case_from_dict(data, Path("ok.json"))
    assert case.id == "ok" and case.locale == "ru"


@pytest.mark.parametrize(
    "mutate,expect",
    [
        (lambda d: d.update({"extra_key": 1}), "unknown top-level key"),
        (lambda d: d["filters"].update({"unknown": 1}), "unknown filters key"),
        (lambda d: d["expectations"].update({"must_contain": ["парк"]}),
         "unknown expectations key"),
        (lambda d: d.pop("expectations"), "missing required key"),
        (lambda d: d["expectations"].pop("in_region"), "missing expectations key"),
        (lambda d: d.update({"id": "different"}), "must equal the file name"),
        (lambda d: d.update({"locale": "de"}), "locale must be one of"),
        (lambda d: d.update({"query": "no"}), "query must be a string of 3..500"),
        (lambda d: d.update({"locale": "en"}), "'en' but the query contains Cyrillic"),
        (lambda d: d.update({"query": "a walk"}), "'ru' but the query contains no Cyrillic"),
        (lambda d: d["expectations"].update({"must_contain_categories": ["зоопарк"]}),
         "not a canonical category code"),
        (lambda d: d["expectations"].update({"expected_status": ["fine"]}),
         "is not one of"),
        (lambda d: d["expectations"].update({"expected_status": []}),
         "expected_status must be a non-empty list"),
        (lambda d: d["expectations"].update({"max_total_minutes": 300}),
         "exceeds the stated budget"),
        (lambda d: d["filters"].update({"interests": ["парк"], "avoid": ["парк"]}),
         "interests and filters.avoid overlap"),
        (lambda d: d["filters"].update({"hard_services": ["туалет"], "avoid": ["туалет"]}),
         "cannot be mandatory and forbidden"),
        (lambda d: d["filters"].update({"origin": {"lat": 1.0, "lon": 23.0}}),
         "origin.lat must be a number in"),
        (lambda d: d["filters"].update({"result_mode": "list"}), "result_mode must be one of"),
        (lambda d: d["filters"].update({"time_budget_minutes": 9999}), "exceeds the API maximum"),
        (lambda d: d["expectations"].update({"must_contain_names": []}),
         "must_contain_names must be a non-empty"),
        (lambda d: d["expectations"].update({"min_places": 0}), "an empty plan would pass"),
        (lambda d: d.update({"parity_group": ""}), "parity_group must be a non-empty string"),
    ],
)
def test_the_validator_rejects_each_schema_violation(mutate, expect):
    data = case_dict({"id": "case1"})
    mutate(data)
    errs = b.validate_golden_case(data, Path("case1.json"))
    assert errs, "a violation must be reported"
    assert any(expect in e for e in errs), f"{expect!r} not in {errs}"


def test_the_validator_reports_every_violation_not_only_the_first():
    data = case_dict({"id": "wrong"})
    data["locale"] = "de"
    errs = b.validate_golden_case(data, Path("case1.json"))
    assert len(errs) >= 2


def test_unknown_expectation_keys_are_refused_so_nothing_is_graded_silently(golden_dir):
    write_case(golden_dir, case_dict({
        "id": "typo", "expectations": {"must_contain_categorie": ["парк"]},
    }))
    with pytest.raises(b.GoldenCaseError) as exc:
        b.load_golden_cases(golden_dir)
    assert "unknown expectations key 'must_contain_categorie'" in str(exc.value)


# --------------------------------------------------------------------------
# the scorer: a good plan is accepted
# --------------------------------------------------------------------------


def test_the_scorer_accepts_a_good_plan():
    case = make_case(
        filters={"hard_services": ["туалет"], "avoid": ["кафе"]},
        expectations={
            "must_contain_categories": ["туалет", "костёл"],
            "must_not_contain_categories": ["кафе"],
            "must_contain_names": ["Фарный"],
            "expected_status": ["ready"],
            "max_total_minutes": 120,
            "in_region": True,
        },
    )
    verdict = verdict_of(case, plan([
        point("Фарный костёл Святого Франциска Ксаверия", category="костёл"),
        point("Туалет у ратуши", latlon=FARNYI, category="туалет", pid=2, visit=0),
    ]))

    assert verdict.passed is True
    assert verdict.reason == "ok"
    assert verdict.status == "ready" and verdict.status_source == "derived:points"
    assert verdict.failed_checks == []
    for code in b.CHECK_PRIORITY:
        assert verdict.checks[code]["ok"] is True, code


def test_a_check_the_response_cannot_answer_is_reported_as_unverified_not_passed():
    case = make_case(expectations={"expected_result_mode": "catalogue"})
    verdict = verdict_of(case, plan([point("Парк Жилибера", category="парк")]))

    assert verdict.passed is True
    assert verdict.unverified_checks == [b.CHECK_RESULT_MODE]
    assert verdict.checks[b.CHECK_RESULT_MODE]["ok"] is True
    assert verdict.checks[b.CHECK_RESULT_MODE]["unverified"] is True


# --------------------------------------------------------------------------
# the scorer: each failure mode is rejected, with the machine-readable reason
# --------------------------------------------------------------------------


def test_the_scorer_rejects_a_missing_mandatory_category():
    case = make_case(
        filters={"hard_services": ["туалет"]},
        expectations={"must_contain_categories": ["туалет"], "expected_status": ["ready"]},
    )
    verdict = verdict_of(case, plan([point("Фарный костёл", category="костёл")]))

    assert verdict.passed is False
    assert verdict.reason == b.CHECK_MISSING_MANDATORY_CATEGORY
    assert "туалет" in verdict.detail
    assert verdict.failed_checks == [b.CHECK_MISSING_MANDATORY_CATEGORY]


def test_the_scorer_rejects_a_forbidden_category_present():
    case = make_case(
        filters={"avoid": ["кафе"]},
        expectations={"must_not_contain_categories": ["кафе"], "expected_status": ["ready"]},
    )
    verdict = verdict_of(case, plan([
        point("Парк Жилибера", category="парк"),
        point("Кафе у парка", category="кафе", pid=2),
    ]))

    assert verdict.passed is False
    assert verdict.reason == b.CHECK_FORBIDDEN_CATEGORY_PRESENT
    assert "кафе" in verdict.detail


def test_the_scorer_rejects_an_out_of_region_point():
    case = make_case(expectations={"expected_status": ["ready"], "in_region": True})
    verdict = verdict_of(case, plan([
        point("Кафедральный собор", latlon=VILNIUS, category="костёл"),
    ]))

    assert verdict.passed is False
    assert verdict.reason == b.CHECK_OUT_OF_REGION_POINT
    assert "outside Grodno ADM1" in verdict.detail


def test_the_scorer_rejects_an_over_budget_total():
    case = make_case(expectations={"max_total_minutes": 120, "expected_status": ["ready"]})
    verdict = verdict_of(case, plan(
        [point("Фарный костёл", category="костёл")], total_minutes=185,
    ))

    assert verdict.passed is False
    assert verdict.reason == b.CHECK_OVER_BUDGET
    assert "185" in verdict.detail and "120" in verdict.detail


def test_the_scorer_measures_the_total_itself_when_the_budget_block_is_missing():
    """An over-budget route must not be able to hide behind a missing field."""
    case = make_case(expectations={"max_total_minutes": 120})
    raw = plan([point("костёл", category="костёл", visit=90)], walk_s=3600.0)
    del raw["budget"]
    verdict = verdict_of(case, raw)
    assert verdict.passed is False
    assert verdict.reason == b.CHECK_OVER_BUDGET  # 60 min walk + 90 min visit

    # ...and with no timing information at all the cap is unverifiable, which is
    # reported rather than counted as a pass.
    bare = {"points": [{"name": "костёл", "category": "костёл", "lat": GRODNO[0],
                        "lon": GRODNO[1]}]}
    silent = verdict_of(case, bare)
    assert silent.passed is True
    assert silent.checks[b.CHECK_OVER_BUDGET]["unverified"] is True


def test_the_scorer_rejects_a_wrong_status():
    case = make_case(expectations={"expected_status": ["needs_clarification"]})
    verdict = verdict_of(case, plan([point("Парк", category="парк")]))

    assert verdict.passed is False
    assert verdict.reason == b.CHECK_WRONG_STATUS
    assert "ready (derived:points) is not one of" in verdict.detail


def test_the_scorer_rejects_a_missing_named_place():
    case = make_case(expectations={"must_contain_names": ["Фарный"], "expected_status": ["ready"]})
    verdict = verdict_of(case, plan([point("Троицкий костёл", category="костёл")]))

    assert verdict.passed is False
    assert verdict.reason == b.CHECK_MISSING_NAMED_PLACE
    assert "Фарный" in verdict.detail


def test_the_scorer_rejects_an_empty_plan_when_the_case_does_not_allow_it():
    case = make_case(expectations={"expected_status": ["ready"], "in_region": True})
    verdict = verdict_of(case, plan([]))

    assert verdict.passed is False
    # The status is derived as infeasible for an empty 200, so the wrong-status
    # check fires first — and the empty plan is reported as its own check.
    assert verdict.reason in (b.CHECK_WRONG_STATUS, b.CHECK_TOO_FEW_PLACES)
    assert verdict.checks[b.CHECK_TOO_FEW_PLACES]["ok"] is False


def test_the_scorer_rejects_too_few_places_even_when_the_plan_is_not_empty():
    case = make_case(expectations={"expected_status": ["ready"], "min_places": 3})
    verdict = verdict_of(case, plan([point("Парк Жилибера", category="парк")]))
    assert verdict.passed is False
    assert verdict.reason == b.CHECK_TOO_FEW_PLACES
    assert "at least 3 required" in verdict.detail


def test_the_out_of_region_case_accepts_a_clean_rejection():
    case = make_case(
        locale="ru",
        query="Вильнюс: Кафедральный собор — маршрут пешком",
        filters={"origin": None, "time_budget_minutes": 120},
        expectations={
            "expected_status": ["rejected", "infeasible", "needs_clarification"],
            "max_total_minutes": 120,
            "in_region": True,
            "allow_empty": True,
        },
    )
    verdict = verdict_of(case, None, http_status=422, api_error="вне области")

    assert verdict.passed is True
    assert verdict.status == "rejected" and verdict.status_source == "http_status"
    assert verdict.checks[b.CHECK_OUT_OF_REGION_POINT]["ok"] is True  # nothing returned
    assert verdict.checks[b.CHECK_TOO_FEW_PLACES]["ok"] is True      # empty allowed


def test_the_out_of_region_case_fails_when_vilnius_points_come_back():
    case = make_case(
        locale="ru",
        query="Вильнюс: Кафедральный собор — маршрут пешком",
        filters={"origin": None},
        expectations={
            "expected_status": ["rejected", "infeasible", "needs_clarification"],
            "max_total_minutes": 120,
            "in_region": True,
            "allow_empty": True,
        },
    )
    verdict = verdict_of(case, plan([point("Кафедральный собор", latlon=VILNIUS)]))
    assert verdict.passed is False
    assert verdict.reason == b.CHECK_WRONG_STATUS  # a 200 plan is not a refusal
    assert verdict.checks[b.CHECK_OUT_OF_REGION_POINT]["ok"] is False


def test_a_5xx_is_an_error_status_and_a_failure_even_for_a_permitting_case():
    case = make_case(expectations={"expected_status": ["rejected", "ready"]})
    verdict = verdict_of(case, None, http_status=503, api_error="valhalla down")
    assert verdict.passed is False
    assert verdict.status == "error"
    assert verdict.reason == b.CHECK_API_ERROR


# --------------------------------------------------------------------------
# status derivation + category reading
# --------------------------------------------------------------------------


def test_status_is_derived_from_the_best_evidence_and_the_source_is_recorded():
    assert b.derive_status(plan([point()])) == ("ready", "derived:points")
    assert b.derive_status(plan([])) == ("infeasible", "derived:empty_plan")
    assert b.derive_status(None, http_status=422) == ("rejected", "http_status")
    assert b.derive_status(None, http_status=500) == ("error", "http_status")
    assert b.derive_status(None, api_error="connection refused") == ("error", "api_error")
    # A real status field wins; a requirements block is the second-best evidence.
    assert b.derive_status({"status": "Catalogue", "points": [point()]}) == (
        "catalogue", "response.status",
    )
    assert b.derive_status({
        "points": [point()],
        "requirements": [
            {"kind": "service", "strength": "hard", "code": "туалет", "status": "unmet"},
        ],
    }) == ("infeasible", "response.requirements")
    assert b.derive_status({
        "requirements": [
            {"kind": "service", "strength": "hard", "code": "туалет", "status": "uncertain"},
        ],
    }) == ("degraded", "response.requirements")
    # A soft requirement that failed is not a degraded plan.
    assert b.derive_status({
        "requirements": [
            {"kind": "interest", "strength": "soft", "code": "кафе", "status": "unmet"},
        ],
    }) == ("ready", "response.requirements")


def test_a_category_is_read_from_a_name_only_when_the_point_has_no_category():
    case = make_case(expectations={
        "must_contain_categories": ["туалет"],
        "must_not_contain_categories": ["кафе"],
    })
    # The point's own category is authoritative: a toilet named "Туалет у кафе"
    # is not a forbidden cafe.
    ok = verdict_of(case, plan([
        point("Туалет у кафе", category="туалет"),
    ]))
    assert ok.passed is True

    # With no category at all the name gets a vote, so a bare "Кафе" is caught.
    bad = verdict_of(case, plan([
        point("Туалет", category="туалет"),
        point("Кафе Лакомка", latlon=FARNYI, pid=2),
    ]))
    assert bad.passed is False
    assert bad.reason == b.CHECK_FORBIDDEN_CATEGORY_PRESENT

    # A word inside a longer word is not a match ("Кафельный" != "кафе").
    assert b.codes_in_name("Кафельный дворик") == set()
    assert b.codes_in_name("Костёл и монастырь") == {"костел", "монастырь"}


def test_build_golden_request_carries_the_explicit_filters_not_prose():
    cases = {c.id: c for c in b.load_golden_cases(GOLDEN_DIR)}
    body = b.build_golden_request(cases["family_walk_ru"])
    assert body["query"] == cases["family_walk_ru"].query
    assert body["locale"] == "ru"
    assert body["party_children"] == 2
    assert body["hard_services"] == ["туалет"]
    assert body["interests"] == ["кафе"]
    assert body["time_budget_minutes"] == 120
    assert body["origin"] == {"lat": 53.6778, "lon": 23.8295}
    assert body["result_mode"] == "route"

    # No origin in the file → no origin on the wire.
    assert "origin" not in b.build_golden_request(cases["vague_no_anchor"])


# --------------------------------------------------------------------------
# parity measured on the responses
# --------------------------------------------------------------------------


def make_parity_pair(filters: dict, expectations: dict) -> list[b.GoldenCase]:
    return [
        make_case("walk_ru", locale="ru", query="прогулка по Гродно с детьми",
                  filters=filters, expectations=expectations, parity_group="walk"),
        make_case("walk_en", locale="en", query="a walk around Grodno with children",
                  filters=filters, expectations=expectations, parity_group="walk"),
    ]


def test_parity_passes_when_both_locales_get_the_same_kind_of_answer():
    members = make_parity_pair(
        {"hard_services": ["туалет"]},
        {"must_contain_categories": ["туалет"], "expected_status": ["ready"]},
    )
    verdicts = {
        "walk_ru": verdict_of(members[0], plan([
            point("Туалет", category="туалет"),
        ])),
        "walk_en": verdict_of(members[1], plan([
            point("Toilet", category="туалет"),
        ])),
    }
    verdict = b.parity_verdict("walk", members, verdicts)
    assert verdict.passed is True
    assert verdict.reason == "ok"
    assert "ru: status=ready" in verdict.detail and "en: status=ready" in verdict.detail


def test_parity_fails_when_one_locale_loses_the_mandatory_category():
    members = make_parity_pair(
        {"hard_services": ["туалет"]},
        {"must_contain_categories": ["туалет"], "expected_status": ["ready"]},
    )
    verdicts = {
        "walk_ru": verdict_of(members[0], plan([point("Туалет", category="туалет")])),
        "walk_en": verdict_of(members[1], plan([point("Park", category="парк")])),
    }
    verdict = b.parity_verdict("walk", members, verdicts)
    assert verdict.passed is False
    assert verdict.reason == b.CHECK_PARITY
    assert "RU/EN parity mismatch" in verdict.detail
    assert b.CHECK_MISSING_MANDATORY_CATEGORY in verdict.detail


def test_parity_reports_the_mandatory_outcome_per_code_not_as_one_flag():
    """A half-satisfied pair must say WHICH code went missing in which locale."""
    members = make_parity_pair(
        {"hard_services": ["туалет", "кафе"]},
        {"must_contain_categories": ["туалет", "кафе"], "expected_status": ["ready"]},
    )
    verdicts = {
        "walk_ru": verdict_of(members[0], plan([
            point("Туалет", category="туалет"),
            point("Кафе", latlon=FARNYI, category="кафе", pid=2),
        ])),
        "walk_en": verdict_of(members[1], plan([point("Toilet", category="туалет")])),
    }
    verdict = b.parity_verdict("walk", members, verdicts)

    assert verdict.passed is False
    assert verdict.reason == b.CHECK_PARITY
    assert "en: status=ready, failed=missing_mandatory_category, " \
           "mandatory=кафе=MISSING, туалет=present" in verdict.detail
    assert "ru: status=ready, failed=none, mandatory=кафе=present, туалет=present" in verdict.detail
    # The evidence rides on the check, not only in the sentence, so the summary
    # and any future consumer read data rather than parsing prose.
    en_checks = verdicts["walk_en"].checks[b.CHECK_MISSING_MANDATORY_CATEGORY]
    assert en_checks["missing"] == ["кафе"] and "туалет" in en_checks["seen"]


def test_parity_fails_when_the_two_locales_get_different_statuses():
    members = make_parity_pair({}, {"expected_status": ["ready", "needs_clarification"]})
    verdicts = {
        "walk_ru": verdict_of(members[0], plan([point("Парк", category="парк")])),
        "walk_en": verdict_of(members[1], plan([])),
    }
    verdict = b.parity_verdict("walk", members, verdicts)
    assert verdict.passed is False
    assert "status=ready" in verdict.detail and "status=infeasible" in verdict.detail


def test_parity_fails_when_a_group_was_not_fully_run():
    members = make_parity_pair({}, {})
    verdict = b.parity_verdict("walk", members, {"walk_ru": verdict_of(members[0], plan([point()]))})
    assert verdict.passed is False
    assert "not fully run" in verdict.detail


def test_the_committed_parity_group_is_checked_on_real_verdicts():
    cases = b.load_golden_cases(GOLDEN_DIR)
    by_id = {c.id: c for c in cases}
    members = [by_id["family_walk_ru"], by_id["family_walk_en"]]
    # RU keeps the toilet, EN loses it: the two locales got different answers to
    # the same request, which is the defect the group exists to catch.
    verdicts = {
        "family_walk_ru": verdict_of(members[0], plan([point("Туалет", category="туалет")])),
        "family_walk_en": verdict_of(members[1], plan([point("Park", category="парк")])),
    }
    verdict = b.parity_verdict("family_walk", members, verdicts)
    assert verdict.passed is False
    assert verdict.reason == b.CHECK_PARITY


# --------------------------------------------------------------------------
# repeats and the aggregate
# --------------------------------------------------------------------------


def test_a_case_passes_only_when_every_repeat_passed():
    case = make_case(expectations={"must_contain_categories": ["туалет"]})
    good = b.GoldenRun(case, 1, {}, verdict=verdict_of(
        case, plan([point("Туалет", category="туалет")])
    ))
    bad = b.GoldenRun(case, 2, {}, verdict=verdict_of(
        case, plan([point("Парк", category="парк")])
    ))

    merged = b.summarise_repeats(case, [good])
    assert merged.passed is True and merged.reason == "ok"

    merged = b.summarise_repeats(case, [good, bad])
    assert merged.passed is False
    assert merged.reason == b.CHECK_MISSING_MANDATORY_CATEGORY
    assert "1/2 repeat(s) passed" in merged.detail
    # The failure survives the merge even though the first repeat was clean.
    assert merged.checks[b.CHECK_MISSING_MANDATORY_CATEGORY]["ok"] is False


def test_the_compliance_rate_counts_cases_and_parity_groups():
    cases = [
        make_case("a", expectations={"expected_status": ["ready"]}),
        make_case("b", expectations={"expected_status": ["ready"]}),
        make_case("c", expectations={"expected_status": ["ready"]}),
    ]
    verdicts = {
        "a": verdict_of(cases[0], plan([point("Парк", category="парк")])),
        "b": verdict_of(cases[1], plan([point("Другое", category="музей")])),
        "c": verdict_of(cases[2], plan([point("Кафе", category="кафе")])),
    }
    assert all(v.passed for v in verdicts.values())
    summary = b.compliance_summary(cases, verdicts, parity=[])
    assert summary["n_cases"] == 3 and summary["n_cases_passed"] == 3
    assert summary["n_units"] == 3
    assert summary["compliance_rate"] == pytest.approx(1.0)
    assert summary["failures_by_reason"] == {}
    assert summary["cases_not_run"] == []

    # One case fails and a parity group fails: 3 of 5 units, and both reasons
    # are counted by code, not by prose.
    verdicts["c"] = verdict_of(
        make_case("c", expectations={"must_contain_categories": ["замок"]}),
        plan([point("Кафе", category="кафе")]),
    )
    parity_failure = b.ComplianceVerdict(
        case_id="parity:p", passed=False, reason=b.CHECK_PARITY, detail="ru != en"
    )
    summary = b.compliance_summary(cases, verdicts, parity=[parity_failure])
    assert summary["n_units"] == 4 and summary["n_units_passed"] == 2
    assert summary["compliance_rate"] == pytest.approx(0.5)
    assert summary["failures_by_reason"] == {
        b.CHECK_MISSING_MANDATORY_CATEGORY: 1, b.CHECK_PARITY: 1,
    }


def test_the_summary_reports_a_case_that_was_never_run():
    cases = [make_case("a"), make_case("b")]
    verdicts = {"a": verdict_of(cases[0], plan([point()]))}
    summary = b.compliance_summary(cases, verdicts, parity=[])
    assert summary["cases_not_run"] == ["b"]
    assert summary["n_cases"] == 1 and summary["compliance_rate"] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# snapshot → replay (offline)
# --------------------------------------------------------------------------


def golden_runs(cases: list[b.GoldenCase], responses: dict[str, dict]) -> list[b.GoldenRun]:
    return [
        b.GoldenRun(
            case=case, repeat=1, request=b.build_golden_request(case),
            http_status=200, response=responses[case.id],
            latency_s=1.0, verdict=verdict_of(case, responses[case.id]),
            ts="2026-09-26T00:00:00+00:00", git_sha="deadbee",
        )
        for case in cases
    ]


def test_a_golden_snapshot_replays_offline_to_the_same_verdicts(tmp_path, golden_dir, monkeypatch):
    write_case(golden_dir, case_dict({
        "id": "toilets", "locale": "ru", "query": "прогулка с обязательным туалетом",
        "filters": {"hard_services": ["туалет"]},
        "expectations": {"must_contain_categories": ["туалет"], "expected_status": ["ready"]},
    }))
    cases = b.load_golden_cases(golden_dir)
    runs = golden_runs(cases, {"toilets": plan([point("Туалет", category="туалет")])})

    snap = tmp_path / "snap"
    path = b.write_golden_snapshot(runs, snap, "http://localhost:8080")
    assert path.name == b.GOLDEN_SNAPSHOT_FILE
    # A golden snapshot must not be readable by the route replay, which globs
    # rows*.jsonl: the two row shapes grade different things.
    with pytest.raises(FileNotFoundError):
        b.load_snapshot(snap)

    def _boom(*a, **k):
        raise AssertionError("golden replay must not touch the agent")

    monkeypatch.setattr(b, "post_generate", _boom)
    monkeypatch.setattr(b, "fetch_health", _boom)

    meta, rows = b.load_golden_snapshot(snap)
    assert meta["schema"] == b.GOLDEN_SNAPSHOT_SCHEMA and len(rows) == 1
    replayed = b.rescore_golden_rows(rows, cases, "http://replay")
    assert len(replayed) == 1
    assert replayed[0].verdict.passed is True
    assert replayed[0].verdict.as_dict() == runs[0].verdict.as_dict()
    assert replayed[0].response == runs[0].response


def test_golden_replay_reports_drift_when_a_case_disappeared(tmp_path, golden_dir):
    write_case(golden_dir, case_dict({"id": "gone", "query": "прогулка по Гродно"}))
    cases = b.load_golden_cases(golden_dir)
    snap = tmp_path / "snap"
    b.write_golden_snapshot(golden_runs(cases, {"gone": plan([point()])}), snap, "http://x")
    with pytest.raises(SystemExit) as exc:
        b.rescore_golden_rows(
            b.load_golden_snapshot(snap)[1], [], "http://x"
        )
    assert "gone" in str(exc.value)


# --------------------------------------------------------------------------
# honesty: no backend, no report
# --------------------------------------------------------------------------


def test_preflight_refuses_when_the_backend_is_unreachable(monkeypatch, capsys):
    def _down(base_url, timeout=5.0):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(b, "fetch_health", _down)
    with pytest.raises(SystemExit) as exc:
        b.preflight_or_exit("http://localhost:8080")
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "backend not reachable" in err
    assert "refusing to run" in err


def test_preflight_refuses_when_valhalla_is_down(monkeypatch, capsys):
    monkeypatch.setattr(b, "fetch_health", lambda *a, **k: {
        "status": "degraded", "db": True, "valhalla": False, "llm": True, "embedder": True,
    })
    with pytest.raises(SystemExit) as exc:
        b.preflight_or_exit("http://localhost:8080")
    assert exc.value.code == 2
    assert "Valhalla is not reachable" in capsys.readouterr().err


def test_preflight_refuses_when_the_database_is_down(monkeypatch, capsys):
    monkeypatch.setattr(b, "fetch_health", lambda *a, **k: {
        "status": "degraded", "db": False, "valhalla": True, "llm": True, "embedder": True,
    })
    with pytest.raises(SystemExit) as exc:
        b.preflight_or_exit("http://localhost:8080")
    assert exc.value.code == 2
    assert "database is not reachable" in capsys.readouterr().err


def test_preflight_passes_and_warns_when_only_the_llm_key_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(b, "fetch_health", lambda *a, **k: {
        "status": "degraded", "db": True, "valhalla": True, "llm": False, "embedder": False,
    })
    health = b.preflight_or_exit("http://localhost:8080")
    assert health["db"] is True
    assert "no LLM/embedder key" in capsys.readouterr().err


def test_the_golden_cli_writes_nothing_when_the_backend_is_down(tmp_path, golden_dir, monkeypatch, capsys):
    write_case(golden_dir, case_dict({"id": "one", "query": "прогулка по Гродно"}))
    out = tmp_path / "out"

    def _down(base_url, timeout=5.0):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(b, "fetch_health", _down)
    with pytest.raises(SystemExit) as exc:
        run_cli(["--golden", "--report-dir", str(out)], monkeypatch, capsys)
    assert exc.value.code == 2
    # No report, not even an empty one: a file outlives the explanation.
    assert not out.exists()
    assert "refusing to run" in capsys.readouterr().err


def test_the_route_harness_also_refuses_to_run_without_a_backend(
    tmp_path, monkeypatch, capsys, routes_dir
):
    """The honesty gate is not golden-only: the route benchmark needs the stack too."""
    def _down(base_url, timeout=5.0):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(b, "fetch_health", _down)
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        run_cli(["--report-dir", str(out)], monkeypatch, capsys)
    assert exc.value.code == 2
    assert not out.exists()
    capsys.readouterr()


# --------------------------------------------------------------------------
# the golden CLI, end to end, with a stubbed stack
# --------------------------------------------------------------------------

HEALTHY = {"status": "ok", "db": True, "valhalla": True, "llm": True, "embedder": True}


@pytest.fixture
def stubbed_stack(monkeypatch):
    """A healthy /health and a canned /routes/generate — still no network."""
    monkeypatch.setattr(b, "fetch_health", lambda *a, **k: dict(HEALTHY))

    def _canned(base_url, payload):
        name = payload["query"]
        if "Фарн" in name:  # the query says "Фарного костёла"
            pts = [point("Фарный костёл Святого Франциска Ксаверия", category="костёл")]
        else:
            pts = [point("Парк Жилибера", category="парк")]
        return 200, plan(pts)

    monkeypatch.setattr(b, "post_generate", _canned)
    return _canned


def test_the_golden_cli_reports_a_passing_case(tmp_path, golden_dir, stubbed_stack, monkeypatch, capsys):
    write_case(golden_dir, case_dict({
        "id": "farnyi", "locale": "ru",
        "query": "прогулка с остановкой у Фарного костёла",
        "expectations": {"must_contain_names": ["Фарный"], "expected_status": ["ready"]},
    }))
    out = tmp_path / "out"
    stdout = run_cli(["--golden", "--report-dir", str(out), "--strict"], monkeypatch, capsys)

    assert "COMPLIANCE RATE: 1.000" in stdout
    assert "farnyi" in stdout and "PASS" in stdout
    report = json.loads((out / "compliance.json").read_text(encoding="utf-8"))
    assert report["summary"]["compliance_rate"] == pytest.approx(1.0)
    assert report["cases"][0]["runs"][0]["verdict"]["passed"] is True
    lines = (out / "compliance.metrics.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["case"] == "__overall__"
    assert "Golden-set compliance report" in (out / "compliance.md").read_text(encoding="utf-8")


def test_the_golden_cli_gates_on_a_failing_case(tmp_path, golden_dir, stubbed_stack, monkeypatch, capsys):
    write_case(golden_dir, case_dict({
        "id": "farnyi", "locale": "ru",
        "query": "прогулка у Фарного костёла",
        "filters": {"hard_services": ["туалет"]},
        "expectations": {
            "must_contain_categories": ["туалет"], "expected_status": ["ready"],
        },
    }))
    out = tmp_path / "out"
    # The canned plan has a костёл and no toilet, so the case fails...
    stdout = run_cli(["--golden", "--report-dir", str(out)], monkeypatch, capsys)
    assert "FAIL" in stdout
    assert b.CHECK_MISSING_MANDATORY_CATEGORY in stdout
    assert "COMPLIANCE RATE: 0.000" in stdout

    # ...and --strict turns that into a non-zero exit.
    with pytest.raises(SystemExit) as exc:
        run_cli(["--golden", "--report-dir", str(out), "--strict"], monkeypatch, capsys)
    assert exc.value.code == 1

    # --min-compliance gates on the rate instead.
    with pytest.raises(SystemExit) as exc:
        run_cli(["--golden", "--report-dir", str(out), "--min-compliance", "0.9"],
                monkeypatch, capsys)
    assert exc.value.code == 1


def test_the_golden_cli_snapshots_rows_that_replay_offline(
    tmp_path, golden_dir, stubbed_stack, monkeypatch, capsys
):
    write_case(golden_dir, case_dict({
        "id": "farnyi", "locale": "ru", "query": "прогулка у Фарного костёла",
        "expectations": {"must_contain_names": ["Фарный"], "expected_status": ["ready"]},
    }))
    snap = tmp_path / "snap"
    out = tmp_path / "out"
    run_cli(["--golden", "--snapshot", str(snap), "--report-dir", str(out)],
            monkeypatch, capsys)
    assert (snap / b.GOLDEN_SNAPSHOT_FILE).exists()

    def _boom(*a, **k):
        raise AssertionError("replay must not call the agent")

    monkeypatch.setattr(b, "post_generate", _boom)
    monkeypatch.setattr(b, "fetch_health", _boom)
    replay_out = tmp_path / "replay"
    stdout = run_cli(["--replay-golden", str(snap), "--report-dir", str(replay_out)],
                     monkeypatch, capsys)
    assert "COMPLIANCE RATE: 1.000" in stdout
    live = json.loads((out / "compliance.json").read_text(encoding="utf-8"))
    again = json.loads((replay_out / "compliance.json").read_text(encoding="utf-8"))
    assert live["summary"]["compliance_rate"] == again["summary"]["compliance_rate"]
    assert [c["runs"][0]["verdict"]["reason"] for c in live["cases"]] == [
        c["runs"][0]["verdict"]["reason"] for c in again["cases"]
    ]


def test_the_golden_cli_rejects_an_unknown_case(golden_dir, stubbed_stack, monkeypatch, capsys):
    write_case(golden_dir, case_dict({"id": "known", "query": "прогулка по Гродно"}))
    with pytest.raises(SystemExit) as exc:
        run_cli(["--golden", "--case", "nope"], monkeypatch, capsys)
    assert exc.value.code == 1
    capsys.readouterr()


# --------------------------------------------------------------------------
# the route harness is untouched by all of the above (regression guard)
# --------------------------------------------------------------------------


def test_the_two_benchmark_sets_stay_separate():
    """golden/*.json must not leak into the reference-walk loader, or vice versa."""
    routes = b.load_golden_routes(b.BENCH_ROUTES)
    cases = b.load_golden_cases(GOLDEN_DIR)
    assert {r.case for r in routes}.isdisjoint({c.id for c in cases})
    # The route rows and the golden rows live in files that cannot be confused.
    assert b.SNAPSHOT_SCHEMA != b.GOLDEN_SNAPSHOT_SCHEMA
    assert b.ROW_SCHEMA != b.GOLDEN_ROW_SCHEMA
    assert b.GOLDEN_SNAPSHOT_FILE != "rows.jsonl"


def test_the_route_scorer_still_scores_a_route(routes_dir):
    """The reference-walk harness keeps grading geometry, golden or not."""
    from tests.test_bench_replay import api_response

    golden = next(r for r in b.load_golden_routes(routes_dir) if r.stops)
    # A returned stop on every reference stop: the committed coordinates are the
    # only ones that fit a committed case (the module's own base point does not).
    pts = [
        {"name": s.name, "lat": s.lat, "lon": s.lon, "visit_minutes": 10, "id": i + 1}
        for i, s in enumerate(golden.stops)
    ]
    result = b.score_response(golden, api_response(pts))
    assert result.recall_at_k == pytest.approx(1.0)
    assert result.hard_failure is False
    # The two graders stay different types with different vocabularies.
    assert set(b.GoldenCase.__dataclass_fields__) != set(
        b.EvaluationResult.__dataclass_fields__
    )
    assert "verdict" not in b.EvaluationResult.__dataclass_fields__

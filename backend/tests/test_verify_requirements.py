"""The independent requirement verifier, and optimizer honesty."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.planner.cost import (
    REASON_MUST_VISIT_UNROUTABLE as COST_REASON_MUST_VISIT_UNROUTABLE,
    REASON_UNROUTABLE_LEG,
    prune_unroutable_stops,
)
from agent.planner.explain import explain
from agent.planner.validate import validate
from agent.planner.verify import (
    REASON_AVOID_OK,
    REASON_AVOID_VIOLATED,
    REASON_CODE_UNKNOWN,
    REASON_GEOMETRY_MISSING,
    REASON_HARD_SERVICE_ABSENT,
    REASON_INTEREST_ABSENT,
    REASON_MUST_VISIT_ABSENT,
    REASON_MUST_VISIT_OK,
    REASON_MUST_VISIT_UNROUTABLE,
    REASON_ROUTE_MISSING,
    REASON_SERVICE_ALONG_ROUTE,
    REASON_SERVICE_NOT_MEASURED,
    REASON_SERVICE_OK,
    REASON_SOFT_SERVICE_ABSENT,
    ServiceAlongEvidence,
    geometry_ok,
    overall_status,
    verify,
    verify_summary,
)
from contracts.planner import Candidate, CostMatrix, ResolvedConstraints
from domain import constants
from domain.requirements import Requirement, TripRequirements


def _cand(pid: int, name: str, category: str | None, lat: float = 53.68, lon: float = 23.83) -> Candidate:
    return Candidate(id=pid, name=name, category=category, lat=lat, lon=lon)


def _geom(n: int = 3) -> dict:
    return {
        "type": "LineString",
        "coordinates": [[23.83 + i * 0.001, 53.68] for i in range(n)],
    }


def _reqs(*requirements: Requirement, budget: int | None = None) -> TripRequirements:
    return TripRequirements(requirements=list(requirements), budget_minutes=budget)


def test_must_visit_on_route_is_satisfied():
    route = [_cand(7, "Старый замок", "замок"), _cand(8, "Костёл", "костёл")]
    reqs = _reqs(
        Requirement(kind="must_visit", strength="hard", name="Старый замок", place_id=7)
    )

    result = verify(reqs, route, _geom())

    assert result[0].status == "satisfied"
    assert result[0].place_ids == [7]
    assert result[0].reason == REASON_MUST_VISIT_OK
    assert reqs.is_ready() is True
    assert overall_status(reqs) == "ready"


def test_must_visit_matched_by_name_when_id_is_unknown():
    route = [_cand(7, "Старый замок", "замок")]
    reqs = _reqs(Requirement(kind="must_visit", strength="hard", name="  старый   ЗАМОК "))

    result = verify(reqs, route, _geom())

    assert result[0].status == "satisfied"
    assert result[0].place_ids == [7]


def test_a_service_beside_the_line_satisfies_the_requirement():
    """«Кофе по пути» — the case that used to be permanently unmet."""
    route = [_cand(1, "Старый замок", "замок")]
    reqs = _reqs(Requirement(kind="service", strength="soft", code="кафе"))
    measured = ServiceAlongEvidence(True, {"кафе": [{"id": 42, "name": "Ссобойка", "off_line_m": 1}]})

    result = verify(reqs, route, _geom(), measured)

    assert result[0].status == "satisfied"
    assert result[0].place_ids == [42]
    assert result[0].reason == REASON_SERVICE_ALONG_ROUTE


def test_a_failed_measurement_is_uncertain_not_unmet():
    route = [_cand(1, "Старый замок", "замок")]
    reqs = _reqs(Requirement(kind="service", strength="hard", code="туалет"))

    result = verify(reqs, route, _geom(), ServiceAlongEvidence(False, {}))

    assert result[0].status == "uncertain"
    assert result[0].reason == REASON_SERVICE_NOT_MEASURED


def test_measured_and_nothing_beside_the_line_is_still_unmet():
    route = [_cand(1, "Старый замок", "замок")]
    reqs = _reqs(Requirement(kind="service", strength="hard", code="туалет"))

    result = verify(reqs, route, _geom(), ServiceAlongEvidence(True, {}))

    assert result[0].status == "unmet"
    assert result[0].reason == REASON_HARD_SERVICE_ABSENT


def test_measured_but_no_line_is_uncertain_not_satisfied():
    route = [_cand(1, "Старый замок", "замок")]
    reqs = _reqs(Requirement(kind="service", strength="soft", code="кафе"))
    measured = ServiceAlongEvidence(True, {"кафе": [{"id": 42, "name": "Ссобойка", "off_line_m": 1}]})

    result = verify(reqs, route, None, measured)

    assert result[0].status == "uncertain"
    assert result[0].reason == REASON_GEOMETRY_MISSING


def test_evidence_for_another_category_does_not_satisfy():
    route = [_cand(1, "Старый замок", "замок")]
    reqs = _reqs(Requirement(kind="service", strength="hard", code="туалет"))
    measured = ServiceAlongEvidence(True, {"кафе": [{"id": 42, "name": "Ссобойка", "off_line_m": 1}]})

    result = verify(reqs, route, _geom(), measured)

    assert result[0].status == "unmet"


def test_hard_service_found_by_category_is_satisfied():
    route = [_cand(1, "Музей", "музей"), _cand(2, "Туалет у ратуши", "туалет")]
    reqs = _reqs(Requirement(kind="service", strength="hard", code="туалет"))

    result = verify(reqs, route, _geom())

    assert result[0].status == "satisfied"
    assert result[0].place_ids == [2]
    assert result[0].reason == REASON_SERVICE_OK


def test_feature_geometry_is_accepted():
    assert geometry_ok({"type": "Feature", "geometry": _geom()}) is True
    assert geometry_ok(_geom()) is True
    assert geometry_ok(None) is False
    assert geometry_ok({}) is False
    assert geometry_ok({"type": "LineString", "coordinates": []}) is False


def test_verify_accepts_a_validated_plan_object():
    from contracts.planner import ValidatedPlan

    route = [_cand(7, "Старый замок", "замок")]
    plan = ValidatedPlan(
        route=route,
        walk_seconds=0.0,
        visit_seconds=0,
        total_seconds=0,
        fits_budget=True,
        stops_dropped=0,
        trace={},
    )
    reqs = _reqs(
        Requirement(kind="must_visit", strength="hard", place_id=7, name="Старый замок")
    )

    result = verify(reqs, plan, _geom())

    assert result[0].status == "satisfied"
    assert result[0].place_ids == [7]


def test_must_visit_absent_is_unmet_and_infeasible():
    route = [_cand(1, "Музей", "музей")]
    reqs = _reqs(Requirement(kind="must_visit", strength="hard", place_id=99, name="Форт"))

    result = verify(reqs, route, _geom())

    assert result[0].status == "unmet"
    assert result[0].reason == REASON_MUST_VISIT_ABSENT
    assert reqs.is_ready() is False
    assert reqs.failed_hard()[0].place_id == 99
    assert overall_status(reqs) == "infeasible"


def test_hard_service_absent_is_unmet_not_success():
    route = [_cand(1, "Музей", "музей")]
    reqs = _reqs(Requirement(kind="service", strength="hard", code="туалет"))

    result = verify(reqs, route, _geom())

    assert result[0].status == "unmet"
    assert result[0].reason == REASON_HARD_SERVICE_ABSENT
    assert reqs.is_ready() is False
    assert overall_status(reqs) == "infeasible"


def test_soft_service_absent_is_unmet_but_does_not_block_readiness():
    route = [_cand(1, "Музей", "музей")]
    reqs = _reqs(Requirement(kind="service", strength="soft", code="кафе"))

    result = verify(reqs, route, _geom())

    assert result[0].status == "unmet"
    assert result[0].reason == REASON_SOFT_SERVICE_ABSENT
    assert reqs.is_ready() is True
    assert overall_status(reqs) == "ready"


def test_interest_absent_is_unmet():
    route = [_cand(1, "Музей", "музей")]
    reqs = _reqs(Requirement(kind="interest", strength="soft", code="замок"))

    result = verify(reqs, route, _geom())

    assert result[0].status == "unmet"
    assert result[0].reason == REASON_INTEREST_ABSENT


def test_avoid_violated_is_unmet():
    route = [_cand(1, "Музей", "музей")]
    reqs = _reqs(Requirement(kind="avoid", strength="hard", code="музей"))

    result = verify(reqs, route, _geom())

    assert result[0].status == "unmet"
    assert result[0].reason == REASON_AVOID_VIOLATED
    assert result[0].place_ids == [1]


def test_avoid_honoured_is_satisfied():
    route = [_cand(1, "Замок", "замок")]
    reqs = _reqs(Requirement(kind="avoid", strength="hard", code="музей"))

    result = verify(reqs, route, _geom())

    assert result[0].status == "satisfied"
    assert result[0].reason == REASON_AVOID_OK


@pytest.mark.parametrize(
    "geometry",
    [None, {}, {"type": "LineString", "coordinates": []}],
    ids=["none", "empty-dict", "empty-coords"],
)
def test_missing_geometry_is_uncertain_never_satisfied(geometry):
    route = [_cand(7, "Старый замок", "замок")]
    reqs = _reqs(
        Requirement(kind="must_visit", strength="hard", place_id=7, name="Старый замок")
    )

    result = verify(reqs, route, geometry)

    assert result[0].status == "uncertain"
    assert result[0].reason == REASON_GEOMETRY_MISSING
    assert result[0].place_ids == [7]
    assert reqs.is_ready() is False
    assert overall_status(reqs) == "degraded"


def test_missing_route_is_uncertain():
    reqs = _reqs(Requirement(kind="service", strength="hard", code="туалет"))

    for empty_plan in (None, [], {"route": []}):
        result = verify(reqs, empty_plan, _geom())
        assert result[0].status == "uncertain"
        assert result[0].reason == REASON_ROUTE_MISSING


def test_unknown_category_code_is_uncertain():
    route = [_cand(1, "Музей", "музей")]
    reqs = _reqs(Requirement(kind="service", strength="hard", code="самокат"))

    result = verify(reqs, route, _geom())

    assert result[0].status == "uncertain"
    assert result[0].reason == REASON_CODE_UNKNOWN
    assert overall_status(reqs) == "degraded"


def test_unroutable_must_visit_reports_infeasible_not_silence():
    route = [_cand(1, "Музей", "музей")]
    plan = {
        "route": route,
        "trace": {
            "unroutable_stops": [
                {"id": 5, "name": "Каплица на острове", "reason": REASON_MUST_VISIT_UNROUTABLE}
            ]
        },
    }
    reqs = _reqs(Requirement(kind="must_visit", strength="hard", place_id=5, name="Каплица"))

    result = verify(reqs, plan, _geom())

    assert result[0].status == "unmet"
    assert result[0].reason == REASON_MUST_VISIT_UNROUTABLE
    assert overall_status(reqs) == "infeasible"


def _island_cost() -> CostMatrix:
    """a→b is unroutable, everything else routes (the road-island case)."""
    bad = float(constants.UNREACHABLE_S)
    return CostMatrix(
        walk_seconds=[[0.0, bad, 600.0], [bad, 0.0, 700.0], [600.0, 700.0, 0.0]],
        visit_minutes=[10, 10, 10],
        indices=[0, 1, 2],
    )


def test_prune_drops_optional_stop_and_returns_reason():
    a = _cand(1, "Волковыск", "памятник")
    b = _cand(2, "Каплица на острове", "костёл", lat=53.007, lon=23.917)
    c = _cand(3, "Гродно", "памятник", lat=53.678, lon=23.827)

    kept, report = prune_unroutable_stops([a, b, c], [a, b, c], _island_cost())

    assert [x.id for x in kept] == [1, 3]
    assert len(report) == 1
    assert report[0].id == 2
    assert report[0].reason == REASON_UNROUTABLE_LEG


def test_prune_never_silently_removes_a_must_visit_stop():
    a = _cand(1, "Волковыск", "памятник")
    b = _cand(2, "Каплица на острове", "костёл", lat=53.007, lon=23.917)
    c = _cand(3, "Гродно", "памятник", lat=53.678, lon=23.827)

    kept, report = prune_unroutable_stops(
        [a, b, c], [a, b, c], _island_cost(), must_visit_ids=[2]
    )

    assert [x.id for x in kept] == [1, 2, 3], "mandatory stop must stay on the route"
    assert [p.id for p in report] == [2]
    assert report[0].reason == COST_REASON_MUST_VISIT_UNROUTABLE


def test_prune_and_verify_agree_on_the_unroutable_reason_code():
    """The pruner's report and the verifier's reason are one machine code."""
    assert COST_REASON_MUST_VISIT_UNROUTABLE == REASON_MUST_VISIT_UNROUTABLE


def test_prune_leaves_a_healthy_tour_untouched():
    a = _cand(1, "A", "замок", lat=53.680, lon=23.830)
    b = _cand(2, "B", "музей", lat=53.681, lon=23.831)
    c = _cand(3, "C", "парк", lat=53.682, lon=23.832)
    cost = CostMatrix(
        walk_seconds=[[0.0, 300.0, 400.0], [300.0, 0.0, 250.0], [400.0, 250.0, 0.0]],
        visit_minutes=[10, 10, 10],
        indices=[0, 1, 2],
    )

    kept, report = prune_unroutable_stops([a, b, c], [a, b, c], cost)

    assert [x.id for x in kept] == [1, 2, 3]
    assert report == []


def test_validate_checks_budget_against_actual_time():
    route = [_cand(1, "Музей", "музей"), _cand(2, "Туалет", "туалет")]
    cost = CostMatrix(
        walk_seconds=[[0.0, 3600.0], [3600.0, 0.0]],
        visit_minutes=[10, 10],
        indices=[0, 1],
    )
    constraints = ResolvedConstraints(time_budget_minutes=30)
    reqs = _reqs(Requirement(kind="service", strength="hard", code="туалет"))

    plan = validate(
        route,
        cost,
        constraints,
        {"order": [0, 1], "algorithm": "brute_open"},
        requirements=reqs,
        geometry=_geom(),
    )

    assert plan.fits_budget is False
    assert plan.trace["budget_exceeded"] is True
    assert plan.trace["requirement_summary"]["status"] == "ready"
    assert plan.trace["requirements"][0]["status"] == "satisfied"
    assert reqs.requirements[0].status == "satisfied"


def test_validate_records_missing_must_visit_as_unmet():
    route = [_cand(1, "Музей", "музей"), _cand(2, "Парк", "парк")]
    cost = CostMatrix(
        walk_seconds=[[0.0, 600.0], [600.0, 0.0]],
        visit_minutes=[10, 10],
        indices=[0, 1],
    )
    constraints = ResolvedConstraints(time_budget_minutes=180)
    reqs = _reqs(Requirement(kind="must_visit", strength="hard", place_id=42, name="Форт"))
    info = {"order": [0, 1], "algorithm": "brute_open", "missing_must_visit_ids": [42]}

    plan = validate(
        route, cost, constraints, info, requirements=reqs, geometry=_geom()
    )

    assert plan.trace["missing_must_visit_ids"] == [42]
    assert reqs.requirements[0].status == "unmet"
    assert reqs.requirements[0].reason == REASON_MUST_VISIT_ABSENT
    assert plan.trace["status"] == "infeasible"


def test_validate_without_requirements_is_unchanged():
    route = [_cand(1, "Музей", "музей"), _cand(2, "Парк", "парк")]
    cost = CostMatrix(
        walk_seconds=[[0.0, 600.0], [600.0, 0.0]],
        visit_minutes=[10, 10],
        indices=[0, 1],
    )
    constraints = ResolvedConstraints(time_budget_minutes=180)

    plan = validate(route, cost, constraints, {"order": [0, 1]})

    assert plan.fits_budget is True
    assert "requirements" not in plan.trace
    assert "requirement_summary" not in plan.trace


def test_explain_includes_the_full_breakdown():
    route = [_cand(1, "Музей", "музей"), _cand(2, "Туалет у ратуши", "туалет")]
    reqs = _reqs(
        Requirement(kind="service", strength="hard", code="туалет"),
        Requirement(kind="service", strength="soft", code="кафе"),
        Requirement(kind="must_visit", strength="soft", place_id=500, name="Где-то"),
        Requirement(kind="service", strength="hard", code="самокат"),
    )
    trace = {"fits_budget": True, "algorithm": "brute_open", "diversity": 1.0}
    verify(reqs, {"route": route, "trace": trace}, _geom())
    trace["requirement_summary"] = verify_summary(reqs)

    text = explain(route, trace, walk_seconds=600)

    assert "Условия запроса:" in text
    assert "выполнено: туалет" in text
    assert "не выполнено:" in text and "кафе" in text and "Где-то" in text
    assert "неизвестно: самокат" in text


def test_explain_has_no_breakdown_without_requirements():
    route = [_cand(1, "Музей", "музей")]
    text = explain(route, {"fits_budget": True, "diversity": 1.0}, walk_seconds=300)

    assert "Условия запроса" not in text

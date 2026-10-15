"""The step-free verdict, and the price a step-free detour is measured at."""

from __future__ import annotations

from agent.tools.plan import _measure_key
from api.models.routes import StepFreeInfo
from api.responses import step_free_info, step_free_sentence
from routing.models import CategoryFacts, Requirement, TripRequirements, WalkRequest
from routing.valhalla.constants import costing_options_for
from routing.verify import (
    REPAIRABLE_REASONS,
    StepsAlong,
    overall_status,
    verify,
    verify_catalogue,
)

FACTS = CategoryFacts()


def _contract(*requirements: Requirement) -> TripRequirements:
    return TripRequirements(requirements=list(requirements))


def _shape() -> dict:
    return {"type": "LineString", "coordinates": [[23.83, 53.68], [23.84, 53.69]]}


def _route(*stops: dict) -> dict:
    return {"route": list(stops), "trace": {}}


def _step(place_id: int) -> dict:
    return {"id": place_id, "name": f"Лестница {place_id}", "category": "лестница"}


def test_a_line_over_a_stairway_is_unmet() -> None:
    contract = _contract(Requirement(kind="step_free", strength="hard"))
    steps = StepsAlong(measured=True, steps=[_step(7), _step(8)])

    verify(contract, _route({"id": 1}), _shape(), facts=FACTS, steps_along=steps)

    verdict = contract.requirements[0]
    assert (verdict.status, verdict.reason) == ("unmet", "steps_on_route")
    assert verdict.place_ids == [7, 8]


def test_a_line_clear_of_stairways_is_proven() -> None:
    contract = _contract(Requirement(kind="step_free", strength="hard"))

    verify(
        contract,
        _route({"id": 1}),
        _shape(),
        facts=FACTS,
        steps_along=StepsAlong(measured=True, steps=[]),
    )

    verdict = contract.requirements[0]
    assert (verdict.status, verdict.reason) == ("satisfied", "step_free_proven")


def test_unread_stairways_are_uncertain_never_unmet() -> None:
    contract = _contract(Requirement(kind="step_free", strength="hard"))

    verify(
        contract,
        _route({"id": 1}),
        _shape(),
        facts=FACTS,
        steps_along=StepsAlong(measured=False, steps=[]),
    )

    verdict = contract.requirements[0]
    assert (verdict.status, verdict.reason) == ("uncertain", "steps_unknown")


def test_no_route_leaves_the_question_open() -> None:
    contract = _contract(Requirement(kind="step_free", strength="hard"))

    verify(contract, _route(), _shape(), facts=FACTS, steps_along=StepsAlong(True, []))

    assert contract.requirements[0].reason == "route_missing"


def test_a_step_free_walk_that_still_climbs_is_repairable() -> None:
    contract = _contract(Requirement(kind="step_free", strength="hard"))

    verify(
        contract,
        _route({"id": 1}),
        _shape(),
        facts=FACTS,
        steps_along=StepsAlong(measured=True, steps=[_step(3)]),
    )

    assert "steps_on_route" in REPAIRABLE_REASONS
    assert "steps_unknown" in REPAIRABLE_REASONS
    assert overall_status(contract) == "degraded"


def test_a_catalogue_cannot_prove_a_walk_step_free() -> None:
    contract = _contract(Requirement(kind="step_free", strength="hard"))

    verify_catalogue(contract, [{"id": 1, "name": "Место"}], facts=FACTS)

    verdict = contract.requirements[0]
    assert (verdict.status, verdict.reason) == ("uncertain", "steps_unknown")


def test_only_a_step_free_pedestrian_asks_for_the_penalty() -> None:
    assert costing_options_for("pedestrian", True) == {"pedestrian": {"step_penalty": 3600.0}}
    assert costing_options_for("pedestrian", False) == {}
    assert costing_options_for("bicycle", True) == {}
    assert costing_options_for("car", True) == {}


def test_the_measure_key_separates_a_step_free_walk() -> None:
    plain = _measure_key(WalkRequest([1, 2, 3]), data_version="v1")
    stepped = _measure_key(WalkRequest([1, 2, 3], step_free=True), data_version="v1")

    assert plain != stepped


def test_the_detour_price_is_reported_in_words() -> None:
    measured = {
        "step_free": {
            "walk_minutes": 24,
            "without_steps_minutes": 1,
            "detour_minutes": 23,
            "length_km": 2.02,
            "without_steps_length_km": 0.095,
        }
    }

    info = step_free_info(measured)
    assert info is not None
    assert (info.walk_minutes, info.without_steps_minutes, info.detour_minutes) == (24, 1, 23)

    sentence = step_free_sentence(info)
    assert "24 мин" in sentence
    assert "1 мин" in sentence
    assert "2.0 км" in sentence


def test_no_step_free_measurement_no_price() -> None:
    assert step_free_info({"walk_minutes": 5}) is None


def test_a_short_detour_reads_in_hours() -> None:
    info = StepFreeInfo(walk_minutes=95, without_steps_minutes=12, detour_minutes=83)

    assert "1 ч 35 мин" in step_free_sentence(info)

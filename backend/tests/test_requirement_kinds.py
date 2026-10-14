"""The requirement kinds that read a fact off the row: admission and opening hours."""

from __future__ import annotations

from datetime import UTC, datetime

from routing.models import CategoryFacts, Requirement, TripRequirements
from routing.verify import overall_status, verify, verify_catalogue

FACTS = CategoryFacts()


def _stop(place_id: int, *, price: str | None = None, hours: str | None = None) -> dict:
    return {
        "id": place_id,
        "name": f"Место {place_id}",
        "category": "музей",
        "ticket_price": price,
        "opening_hours": hours,
    }


def _contract(*requirements: Requirement, at: datetime | None = None) -> TripRequirements:
    return TripRequirements(requirements=list(requirements), at=at)


def _shape() -> dict:
    return {"type": "LineString", "coordinates": [[23.83, 53.68], [23.84, 53.69]]}


def _route(*stops: dict) -> dict:
    return {"route": list(stops), "trace": {}}


def test_every_stop_proven_free_is_free() -> None:
    contract = _contract(Requirement(kind="free", strength="hard"))

    verify(
        contract,
        _route(_stop(1, price="вход свободный"), _stop(2, price="no")),
        _shape(),
        facts=FACTS,
    )

    verdict = contract.requirements[0]
    assert (verdict.status, verdict.reason) == ("satisfied", "free_entry_proven")
    assert verdict.place_ids == [1, 2]


def test_one_paid_stop_is_the_answer() -> None:
    contract = _contract(Requirement(kind="free", strength="hard"))

    verify(contract, _route(_stop(1, price="no"), _stop(2, price="≈7 BYN")), _shape(), facts=FACTS)

    verdict = contract.requirements[0]
    assert (verdict.status, verdict.reason) == ("unmet", "paid_stop_on_route")
    assert verdict.place_ids == [2]
    assert overall_status(contract) == "degraded"


def test_a_price_nobody_wrote_down_is_not_a_free_one() -> None:
    contract = _contract(Requirement(kind="free", strength="hard"))

    verify(contract, _route(_stop(1, price="no"), _stop(2)), _shape(), facts=FACTS)

    verdict = contract.requirements[0]
    assert (verdict.status, verdict.reason) == ("uncertain", "admission_unknown")
    assert verdict.place_ids == [2]


def test_a_soft_cost_condition_does_not_hold_the_plan_back() -> None:
    contract = _contract(Requirement(kind="free", strength="soft"))

    verify(contract, _route(_stop(1, price="20 BYN")), _shape(), facts=FACTS)

    assert contract.requirements[0].status == "unmet"
    assert overall_status(contract) == "ready"


def test_a_catalogue_only_has_to_hold_a_free_place() -> None:
    contract = _contract(Requirement(kind="free", strength="hard"))
    places = [_stop(1, price="5 BYN"), _stop(2, price="вход свободный")]

    verify_catalogue(contract, places, facts=FACTS)

    verdict = contract.requirements[0]
    assert (verdict.status, verdict.reason) == ("satisfied", "free_entry_proven")
    assert verdict.place_ids == [2]


def test_a_catalogue_of_nothing_but_paid_places_is_unmet() -> None:
    contract = _contract(Requirement(kind="free", strength="hard"))

    verify_catalogue(contract, [_stop(1, price="5 BYN")], facts=FACTS)

    assert contract.requirements[0].reason == "paid_stop_on_route"


def test_a_walk_inside_the_hours_is_open() -> None:
    at = datetime(2026, 10, 19, 18, 30, tzinfo=UTC)
    contract = _contract(Requirement(kind="open", strength="hard"), at=at)

    verify(
        contract,
        _route(_stop(1, hours="Mo-Su 10:00-22:00"), _stop(2, hours="24/7")),
        _shape(),
        facts=FACTS,
        at=at,
    )

    verdict = contract.requirements[0]
    assert (verdict.status, verdict.reason) == ("satisfied", "open_at_visit")


def test_a_stop_closed_at_that_hour_is_the_answer() -> None:
    at = datetime(2026, 10, 19, 21, 30, tzinfo=UTC)
    contract = _contract(Requirement(kind="open", strength="hard"), at=at)

    verify(
        contract,
        _route(_stop(1, hours="24/7"), _stop(2, hours="ежедневно 9:00–18:00")),
        _shape(),
        facts=FACTS,
        at=at,
    )

    verdict = contract.requirements[0]
    assert (verdict.status, verdict.reason) == ("unmet", "closed_at_visit")
    assert verdict.place_ids == [2]


def test_hours_the_code_does_not_read_stay_unknown() -> None:
    at = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    contract = _contract(Requirement(kind="open", strength="hard"), at=at)

    verify(
        contract,
        _route(_stop(1, hours="снаружи — в любое время")),
        _shape(),
        facts=FACTS,
        at=at,
    )

    assert contract.requirements[0].reason == "hours_unknown"


def test_without_a_moment_the_question_cannot_be_answered() -> None:
    contract = _contract(Requirement(kind="open", strength="hard"))

    verify(contract, _route(_stop(1, hours="24/7")), _shape(), facts=FACTS)

    assert contract.requirements[0].reason == "visit_time_unknown"


def test_the_same_moment_answers_the_same_twice() -> None:
    at = datetime(2026, 10, 17, 1, 30, tzinfo=UTC)
    stops = _route(_stop(1, hours="Su-Th 09:00-23:00; Fr-Sa 09:00-01:00"))

    first = _contract(Requirement(kind="open", strength="hard"), at=at)
    second = _contract(Requirement(kind="open", strength="hard"), at=at)
    verify(first, stops, _shape(), facts=FACTS, at=at)
    verify(second, stops, _shape(), facts=FACTS, at=at)

    assert first.requirements[0].model_dump() == second.requirements[0].model_dump()
    assert first.requirements[0].reason == "closed_at_visit"


def test_a_stop_beside_the_line_does_not_prove_a_walk() -> None:
    contract = _contract(Requirement(kind="free", strength="hard"))

    verify(
        contract,
        _route(_stop(1, price="no")),
        {"type": "Point", "coordinates": [23.8, 53.6]},
        facts=FACTS,
    )

    assert contract.requirements[0].reason == "geometry_missing"

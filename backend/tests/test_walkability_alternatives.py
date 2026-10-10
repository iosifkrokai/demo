"""When the plan outgrows the chosen profile, the answer must say so and offer a way."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import constants
from planner.pipeline import alternatives_for, alternatives_sentence


def _costings(offers) -> list[str]:
    return [o.costing for o in offers]


def test_a_city_walk_offers_nothing() -> None:
    """Inside the bounds the answer is the answer — no noise."""
    offers = alternatives_for(costing="pedestrian", walk_s=45 * 60, length_km=3.4)
    assert offers == []


def test_a_region_wide_walk_offers_a_bicycle_and_a_car() -> None:
    """The measured case: 17 hours and 211 km on foot."""
    offers = alternatives_for(costing="pedestrian", walk_s=17 * 3600, length_km=211.7)
    assert _costings(offers) == ["bicycle", "auto"], "foot needs both offers"
    assert all(o.reason == "too_far_to_walk" for o in offers)
    assert all(o.note for o in offers), "every offer carries the tourist's sentence"


def test_distance_alone_is_enough_to_offer() -> None:
    """Far but short: the walk is still not one to take (a 20-km straight line)."""
    offers = alternatives_for(
        costing="pedestrian", walk_s=30 * 60, length_km=constants.WALK_TOO_FAR_KM + 5
    )
    assert _costings(offers) == ["bicycle", "auto"]
    assert offers[0].reason == "too_far_to_walk"


def test_time_alone_is_enough_to_offer() -> None:
    """Long but local: many stops on foot still stops being a walk."""
    offers = alternatives_for(
        costing="pedestrian", walk_s=(constants.WALK_TOO_LONG_MINUTES + 30) * 60, length_km=4.0
    )
    assert _costings(offers) == ["bicycle", "auto"]
    assert offers[0].reason == "too_long_to_walk"


def test_a_bicycle_plan_offers_a_car_only() -> None:
    """Bicycle is already the faster suggestion; there is nothing smaller to offer."""
    offers = alternatives_for(costing="bicycle", walk_s=9 * 3600, length_km=180.0)
    assert _costings(offers) == ["auto"]


def test_a_motorised_plan_offers_nothing() -> None:
    costs = ("auto", "car", "truck", "bus", "motorcycle", "motor_scooter")
    for costing in costs:
        assert alternatives_for(costing=costing, walk_s=9 * 3600, length_km=180.0) == [], costing


def test_transit_is_never_offered_as_a_costing_we_cannot_route() -> None:
    """A suggestion the client would submit back must be one Valhalla can serve."""
    offers = alternatives_for(costing="pedestrian", walk_s=17 * 3600, length_km=211.7)
    assert "bus" not in _costings(offers)
    sentence = alternatives_sentence(offers, 17 * 3600)
    assert "автобус" in sentence and "такси" in sentence


def test_the_sentence_states_the_time_and_nothing_when_there_is_nothing() -> None:
    assert alternatives_sentence([], 3600) == ""
    offers = alternatives_for(costing="pedestrian", walk_s=17 * 3600, length_km=211.7)
    sentence = alternatives_sentence(offers, 17 * 3600)
    assert "17.0 ч" in sentence, sentence

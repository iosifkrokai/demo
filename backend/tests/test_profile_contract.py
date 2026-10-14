"""The profile the tourist chose must reach the measurement, and the engine must
hear a name it knows.
"""

from __future__ import annotations

import json
from typing import Any, get_args

import pytest

from agent.mapping import _merge
from agent.models import ReaderBrief
from agent.schema import PlanSubmission
from api.models.routes import TransportLiteral
from core import constants
from core.errors import UpstreamUnavailableError
from routing.cost import _synthetic_cost
from routing.geometry import distance_m
from routing.models import Candidate, CategoryFacts
from routing.optimize import _max_leg_seconds
from routing.valhalla import http as valhalla_http, route as valhalla_route, snap as valhalla_snap
from routing.valhalla.constants import VALHALLA_COSTINGS, costing_for


def _candidate(pid: int, lat: float, lon: float) -> Candidate:
    return Candidate(id=pid, name=f"P{pid}", category=None, lat=lat, lon=lon)


def _contract(brief_profile: str | None, submitted_profile: str) -> Any:
    brief = ReaderBrief(locale="ru", profile=brief_profile)
    submission = PlanSubmission(profile=submitted_profile)
    return _merge("прогулка", brief, submission, set(), None)


def test_the_tourists_transport_beats_the_models_guess() -> None:
    """A bicycle request measured as a walk is a wrong answer, not a cheap one."""
    assert _contract("bicycle", "pedestrian").costing == "bicycle"
    assert _contract("auto", "pedestrian").costing == "auto"


def test_the_models_transport_stands_only_when_no_control_was_used() -> None:
    """The «any» transport is the one case where the reading decides."""
    assert _contract(None, "bicycle").costing == "bicycle"


def _mode(brief_mode: str | None, outcome: str | None) -> str:
    brief = ReaderBrief(locale="ru", result_mode=brief_mode)
    return _merge("прогулка", brief, PlanSubmission(outcome=outcome), set(), None).result_mode


def test_a_result_mode_the_tourist_chose_beats_the_models_reading() -> None:
    """«Каталог» picked in the interface is a list, whatever the model made of it."""
    assert _mode("catalogue", "route") == "catalogue"
    assert _mode("route", "catalogue") == "route"


def test_a_result_mode_nobody_chose_is_the_models_to_read() -> None:
    assert _mode(None, "catalogue") == "catalogue"
    assert _mode(None, None) == "route"


@pytest.mark.parametrize(
    ("profile", "costing"),
    [
        ("car", "auto"),
        ("drive", "auto"),
        ("walk", "pedestrian"),
        (" CAR ", "auto"),
        ("pedestrian", "pedestrian"),
        (None, "pedestrian"),
    ],
)
def test_a_alias_resolves_to_the_costings_the_engine_knows(profile, costing) -> None:
    assert costing_for(profile) == costing


def test_a_name_the_stack_cannot_route_is_not_passed_through() -> None:
    """`multimodal` needs transit tiles this build does not have — it can only 400."""
    assert "multimodal" not in VALHALLA_COSTINGS
    assert costing_for("multimodal") == "pedestrian"
    assert costing_for("телепорт") == "pedestrian"


def test_every_profile_the_wire_offers_is_routable() -> None:
    """The wire must not advertise a transport whose costing the engine rejects."""
    for profile in get_args(TransportLiteral):
        assert costing_for(profile) in VALHALLA_COSTINGS


def test_the_leg_rule_follows_the_costing_not_the_word() -> None:
    """Otherwise `profile="walk"` is measured as a walk but capped as a drive."""
    assert _max_leg_seconds(3600, "walk") == _max_leg_seconds(3600, "pedestrian")
    assert _max_leg_seconds(3600, "car") == _max_leg_seconds(3600, "auto")
    longest = constants.MAX_BUDGET_MIN * 60
    assert _max_leg_seconds(longest, "pedestrian") != _max_leg_seconds(longest, "bicycle")


def test_a_bicycle_leg_is_capped_by_riding_distance_not_by_the_budget() -> None:
    """A bicycle used to get the driven rule: one leg could be the whole budget."""
    ride = (constants.BICYCLE_LEG_KM / constants.BICYCLE_SPEED_KMH) * 3600
    longest = constants.MAX_BUDGET_MIN * 60

    assert _max_leg_seconds(longest, "bicycle") == pytest.approx(ride)
    assert _max_leg_seconds(None, "bicycle") == pytest.approx(ride)
    assert _max_leg_seconds(None, "bicycle") < constants.DRIVE_LEG_FALLBACK_S
    assert _max_leg_seconds(3 * 3600, "auto") == pytest.approx(3 * 3600)


def test_the_synthetic_fallback_walks_at_the_pedestrian_speed() -> None:
    """The offline fallback is a measurement too: an alias must not change its pace,
    and each transport moves at one reviewable speed rather than at 4.68 or 28.8."""
    a, b = _candidate(1, 53.6771, 23.8258), _candidate(2, 53.6819, 23.8400)
    facts = CategoryFacts.from_rows([])
    metres = distance_m(a, b)

    pedestrian = _synthetic_cost([a, b], "pedestrian", facts).walk_seconds
    alias = _synthetic_cost([a, b], "walk", facts).walk_seconds
    bicycle = _synthetic_cost([a, b], "bicycle", facts).walk_seconds

    assert pedestrian[0][1] == pytest.approx(metres / (constants.PEDESTRIAN_SPEED_KMH / 3.6))
    assert alias == pedestrian
    assert bicycle[0][1] == pytest.approx(metres / (constants.BICYCLE_SPEED_KMH / 3.6))


def test_the_ordering_pass_hands_the_engine_a_costing_it_knows(monkeypatch) -> None:
    """`/optimized_route` was the one boundary that sent a raw alias — and a 400
    there is swallowed, so the ordering was silently skipped for `car`."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        valhalla_snap, "snap_locations", lambda locations, costing, timeout: list(locations)
    )

    def capture(method: str, url: str, *, params: dict[str, Any], timeout: float, **_: Any) -> Any:
        seen.update(json.loads(params["json"]))
        raise UpstreamUnavailableError("captured")

    monkeypatch.setattr(valhalla_http, "request_with_retry", capture)

    locations = [{"lat": 53.6771, "lon": 23.8258}, {"lat": 53.6819, "lon": 23.8400}]
    with pytest.raises(UpstreamUnavailableError):
        valhalla_route.optimized_route(locations, costing="car")

    assert seen["costing"] == "auto"

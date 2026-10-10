"""The catalogue: «что показать: каталог» is a list, not a route.

A catalogue request carries places and no geometry; verification is membership-only.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from contracts.planner import (
    Candidate,
    IntentDecision,
    IntentResult,
    ResolvedConstraints,
)
from domain.requirements import Requirement, TripRequirements
from planner import pipeline as pipeline_mod
from planner.verify import (
    REASON_INTEREST_IN_CATALOGUE,
    REASON_MUST_VISIT_ABSENT,
    REASON_MUST_VISIT_IN_CATALOGUE,
    REASON_ROUTE_MISSING,
    REASON_SERVICE_IN_CATALOGUE,
    overall_status,
    verify_catalogue,
)


def _cand(pid: int, name: str, category: str, town: str, relevance: float = 1.0) -> Candidate:
    return Candidate(
        id=pid, name=name, category=category, lat=53.68, lon=23.83,
        town=town, relevance=relevance,
    )


def _reqs(*requirements: Requirement) -> TripRequirements:
    return TripRequirements(requirements=list(requirements))


def test_catalogue_satisfies_a_must_visit_by_membership():
    places = [_cand(7, "Старый замок", "замок", "Гродно")]
    reqs = _reqs(Requirement(kind="must_visit", strength="hard", place_id=7, name="Старый замок"))

    result = verify_catalogue(reqs, places)

    assert result[0].status == "satisfied"
    assert result[0].reason == REASON_MUST_VISIT_IN_CATALOGUE
    assert result[0].place_ids == [7]
    assert overall_status(reqs) == "ready"


def test_catalogue_never_says_on_the_route():
    """The satisfaction reasons are catalogue-specific: no route was walked."""
    places = [_cand(7, "Старый замок", "замок", "Гродно")]
    reqs = _reqs(
        Requirement(kind="must_visit", strength="hard", place_id=7, name="Старый замок"),
        Requirement(kind="interest", strength="soft", code="замок"),
        Requirement(kind="service", strength="soft", code="замок"),
    )

    verify_catalogue(reqs, places)

    assert reqs.requirements[1].reason == REASON_INTEREST_IN_CATALOGUE
    assert reqs.requirements[2].reason == REASON_SERVICE_IN_CATALOGUE


def test_catalogue_reports_a_missing_must_visit_as_unmet_not_uncertain():
    places = [_cand(1, "Музей", "музей", "Гродно")]
    reqs = _reqs(Requirement(kind="must_visit", strength="hard", place_id=99, name="Форт"))

    result = verify_catalogue(reqs, places)

    assert result[0].status == "unmet"
    assert result[0].reason == REASON_MUST_VISIT_ABSENT
    assert overall_status(reqs) == "infeasible"


def test_catalogue_of_nothing_is_uncertain_not_unmet():
    """An empty list means «мы ничего не предложили», not «данных нет»."""
    reqs = _reqs(Requirement(kind="must_visit", strength="hard", place_id=7, name="Старый замок"))

    result = verify_catalogue(reqs, [])

    assert result[0].status == "uncertain"
    assert result[0].reason == REASON_ROUTE_MISSING


def _catalogue(candidates: list[Candidate], requirements: TripRequirements):
    pipeline = pipeline_mod.Pipeline(db=object())
    return pipeline._catalogue_response(
        req=pipeline_mod.GenerateReq(query="все костёлы Гродненской области", result_mode="catalogue"),
        requirements=requirements,
        intent=IntentResult(decision=IntentDecision(), source="agent"),
        constraints=ResolvedConstraints(),
        candidates=candidates,
        t0=0.0,
    )


def test_catalogue_response_has_places_and_no_geometry():
    places = [
        _cand(1, "Фарный костёл", "костёл", "Гродно"),
        _cand(2, "Костёл в Лиде", "костёл", "Лида"),
    ]
    reqs = _reqs(Requirement(kind="interest", strength="soft", code="костёл"))

    response = _catalogue(places, reqs)

    assert len(response.points) == 2
    assert response.shape == {}, "a catalogue has no line to draw"
    assert response.budget is None
    assert response.summary.time_seconds is None and response.summary.length_km is None
    assert response.result_mode == "catalogue"
    assert response.debug["result_mode"] == "catalogue"
    assert response.debug["towns"] == ["Гродно", "Лида"]
    assert response.interpretation is not None
    assert response.interpretation.status == "ready"


def test_a_plain_route_is_the_default_mode_so_old_clients_see_no_change():
    """Only a catalogue opts in; every other answer keeps saying "route"."""
    from contracts.planner import RouteResponse

    assert RouteResponse.model_fields["result_mode"].default == "route"


def test_catalogue_groups_by_town_and_survives_an_unbudgeted_request():
    """Town order first, then relevance — and nothing is trimmed by a budget."""
    places = [
        _cand(1, "A", "костёл", "Лида", relevance=0.2),
        _cand(2, "B", "костёл", "Гродно", relevance=0.9),
        _cand(3, "C", "костёл", "Гродно", relevance=0.5),
    ]

    response = _catalogue(places, _reqs())

    assert [p.town for p in response.points] == ["Гродно", "Гродно", "Лида"]
    assert [p.name for p in response.points] == ["B", "C", "A"], "relevance inside a town"
    assert len(response.points) == 3, "a catalogue is not trimmed"


def test_catalogue_requires_a_result_mode_field_to_opt_in():
    """The default is still a route: the catalogue is not a silent switch."""
    field = pipeline_mod.GenerateReq.model_fields["result_mode"]
    assert field.default == "route"

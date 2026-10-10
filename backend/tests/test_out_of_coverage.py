"""A request about another country is refused, not answered with look-alikes."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.planner.intent import mark_out_of_coverage
from agent.planner.pipeline import _outside_left_unresolved
from agent.planner.resolve import _same_name
from agent.planner.verify import (
    INFEASIBLE_REASONS,
    REASON_CODES,
    REASON_MUST_VISIT_OUTSIDE,
    overall_status,
    verify,
)
from contracts.planner import Candidate, ResolvedConstraints
from domain.requirements import (
    REASON_MUST_VISIT_OUTSIDE as CONTRACT_REASON,
    Requirement,
    TripRequirements,
)

VILNIUS_CATHEDRAL = "Кафедральный собор Святого Станислава"
LIDA_CATHEDRAL = "Кафедральный собор святого Архангела Михаила"


def _cand(pid: int, name: str, category: str | None = "церковь") -> Candidate:
    return Candidate(id=pid, name=name, category=category, lat=53.89, lon=25.30)


def _geom(n: int = 3) -> dict:
    return {
        "type": "LineString",
        "coordinates": [[25.30 + i * 0.001, 53.89] for i in range(n)],
    }


def test_the_reason_is_structural_and_therefore_infeasible():
    """One code, in the vocabulary, and among the reasons that mean "cannot be".
    A hard requirement unmet for a structural reason has always meant `infeasible`.
    """
    assert CONTRACT_REASON == "must_visit_outside_coverage"
    assert REASON_MUST_VISIT_OUTSIDE in REASON_CODES
    assert REASON_MUST_VISIT_OUTSIDE in INFEASIBLE_REASONS


def test_a_name_outside_the_region_makes_the_request_infeasible():
    reqs = TripRequirements()
    mark_out_of_coverage(reqs, [VILNIUS_CATHEDRAL])

    result = verify(reqs, [], _geom())

    assert result[0].status == "unmet"
    assert result[0].reason == REASON_MUST_VISIT_OUTSIDE
    assert result[0].strength == "hard"
    assert overall_status(reqs) == "infeasible"


def test_the_contract_keeps_what_the_tourist_asked_for():
    """Marking is not rewriting: the name stays verbatim, once."""
    reqs = TripRequirements(
        requirements=[
            Requirement(kind="must_visit", name=VILNIUS_CATHEDRAL, source="text")
        ]
    )

    mark_out_of_coverage(reqs, [VILNIUS_CATHEDRAL, VILNIUS_CATHEDRAL])

    assert [r.name for r in reqs.requirements] == [VILNIUS_CATHEDRAL]
    assert reqs.requirements[0].strength == "hard"
    assert reqs.requirements[0].reason == REASON_MUST_VISIT_OUTSIDE


def test_a_look_alike_inside_the_region_cannot_satisfy_it():
    """The crux: the substituted place is in the plan and must prove nothing.
    Even standing on the route it is a different place and the verdict stays `unmet`.
    """
    reqs = TripRequirements()
    mark_out_of_coverage(reqs, [VILNIUS_CATHEDRAL])
    reqs.requirements[0].place_id = 554
    route = [_cand(554, LIDA_CATHEDRAL)]

    result = verify(reqs, route, _geom())

    assert result[0].status == "unmet"
    assert result[0].reason == REASON_MUST_VISIT_OUTSIDE
    assert result[0].place_ids == []


def test_a_flagged_name_that_did_resolve_in_region_is_not_a_refusal():
    """A wrong reading must not cost a servable request its route.
    The check is whether the name grounded to a real place inside the region.
    """
    reqs = TripRequirements(outside_coverage=["Старый замок"])
    constraints = ResolvedConstraints(resolved_names=["Старый замок"])

    assert _outside_left_unresolved(reqs, constraints) == []


def test_an_unresolvable_flagged_name_is_a_refusal():
    reqs = TripRequirements(outside_coverage=[VILNIUS_CATHEDRAL, "Вильнюс"])
    constraints = ResolvedConstraints(resolved_names=[])

    assert _outside_left_unresolved(reqs, constraints) == [
        VILNIUS_CATHEDRAL,
        "Вильнюс",
    ]


def test_nothing_flagged_means_no_question_at_all():
    assert _outside_left_unresolved(TripRequirements(), ResolvedConstraints()) == []


def test_strict_matching_takes_the_same_name_and_nothing_else():
    """What a flagged name may resolve to: itself, with a town suffix.
    When the reader says the name is not in this region, only the name itself counts.
    """
    assert _same_name("Старый замок (Гродно)", "Старый замок") is True
    assert _same_name("Старый замок", "старый  ЗАМОК") is True
    assert _same_name("  Костёл   Святого   Франциска  ", "Костёл Святого Франциска")
    assert _same_name(LIDA_CATHEDRAL, VILNIUS_CATHEDRAL) is False
    assert _same_name("Фарный костёл", "Костёл") is False
    assert _same_name("", "Старый замок") is False

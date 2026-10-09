"""A request about another country is refused, not answered with look-alikes.

The case that produced this module: «Вильнюс: Кафедральный собор Святого
Станислава — маршрут пешком» was answered ``ready`` with four stops in Лида. The
reading had correctly said the object is in Lithuania; the name matcher then
accepted a *different* cathedral (id 554, «Кафедральный собор святого Архангела
Михаила», Лида) as the named place, and the route was planned around it. A plan
that looks complete and leads a tourist to another town's landmarks under the
name they asked for is worse than no plan at all.

What is pinned here:

  * a name the reading placed outside the region makes the request infeasible —
    through the verifier's ordinary rule, not a special case;
  * a look-alike inside the region can never satisfy that requirement, even when
    it is standing in the plan;
  * the cross-check that keeps a wrong reading harmless: a flagged name that did
    resolve to a real place inside the region is not a refusal;
  * the strict name matching those flags switch on.

No network, no DB: plans, routes and contracts are built by hand.
"""

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

    The status is not decided by the refusal path: a hard requirement unmet for a
    structural reason has always meant `infeasible`, and the client localises the
    code rather than reading Russian prose.
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

    id 554 is the Lida cathedral the matcher had accepted. Even standing on the
    route — which is exactly what used to happen — it is a different place, in a
    different town, and the verdict stays `unmet`.
    """
    reqs = TripRequirements()
    mark_out_of_coverage(reqs, [VILNIUS_CATHEDRAL])
    # The substitution the gate exists to stop, reproduced: the resolved id
    # attached to a stop that really is on the route.
    reqs.requirements[0].place_id = 554
    route = [_cand(554, LIDA_CATHEDRAL)]

    result = verify(reqs, route, _geom())

    assert result[0].status == "unmet"
    assert result[0].reason == REASON_MUST_VISIT_OUTSIDE
    assert result[0].place_ids == []


def test_a_flagged_name_that_did_resolve_in_region_is_not_a_refusal():
    """A wrong reading must not cost a servable request its route.

    The model can flag «Старый замок» by mistake; the man-made check is whether
    the name grounded to a real place inside the region. It did — so no refusal.
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

    Similarity is how a Lida cathedral became «Кафедральный собор Святого
    Станислава»; when the reader has already said the name is not in this region,
    only the name itself counts.
    """
    assert _same_name("Старый замок (Гродно)", "Старый замок") is True
    assert _same_name("Старый замок", "старый  ЗАМОК") is True
    assert _same_name("  Костёл   Святого   Франциска  ", "Костёл Святого Франциска")
    # The substitution itself, and its neighbours, must not pass.
    assert _same_name(LIDA_CATHEDRAL, VILNIUS_CATHEDRAL) is False
    assert _same_name("Фарный костёл", "Костёл") is False
    assert _same_name("", "Старый замок") is False

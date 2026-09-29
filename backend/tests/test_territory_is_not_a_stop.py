"""A territory is not a stop.

The defect this pins was found by the stage evals, not by reasoning: asked
«Гродно за два часа», the model reads «visit Grodno» and offers a must-visit
named «Гродно». Nothing in the dataset is called «Гродно», so the deterministic
verifier could only ever report it `unmet` — and it did, in almost every answer,
which made honest reporting look broken (the owner saw it as «лишний шум»).

A territory now becomes no requirement at all. It is deliberately *not* recorded
in ``areas`` either: those are the sub-areas the contract knows ("старый город" →
`grodno-old-town`), and the contract is explicit that «замки Гродно» names no
area — the city scope already follows from the query text and the region
geo-fence. A place with a real id is untouched.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.planner.agent_interpret import (
    AgentReading,
    AgentRequirement,
    _reading_requirements,
)


def _reading(*items: AgentRequirement) -> AgentReading:
    return AgentReading(requirements=list(items))


def test_a_city_becomes_the_search_area_not_a_mandatory_stop():
    reading = _reading(
        AgentRequirement(kind="must_visit", strength="hard", name="Гродно")
    )

    requirements, unsupported = _reading_requirements(
        reading, "Гродно за два часа", observed=set()
    )

    assert requirements == [], "город не может стать обязательной остановкой"
    # It is not an «ask we could not represent» either: nothing was asked for
    # that the system failed to represent — «Гродно» is simply where to look.
    assert unsupported == []


def test_an_oblast_name_is_folded_too():
    reading = _reading(
        AgentRequirement(kind="must_visit", strength="hard", name="Гродненская область")
    )
    requirements, unsupported = _reading_requirements(
        reading, "замки Гродненской области", observed=set()
    )
    assert requirements == []
    assert unsupported == []


def test_a_real_place_is_still_a_mandatory_stop():
    # The id was observed in this run, so the place exists — nothing is folded.
    reading = _reading(
        AgentRequirement(
            kind="must_visit", strength="hard", name="Старый замок", place_id=7
        )
    )
    requirements, _ = _reading_requirements(
        reading, "Старый замок и кофе", observed={7}
    )
    assert [r.name for r in requirements] == ["Старый замок"]
    assert requirements[0].place_id == 7


def test_a_name_that_is_neither_a_place_nor_an_area_stays_visible():
    # «Старый» is a fragment of a real name: not an area, no id — it must NOT be
    # silently dropped, because that is a defect in the reading, not a territory.
    reading = _reading(
        AgentRequirement(kind="must_visit", strength="hard", name="Старый")
    )
    requirements, _ = _reading_requirements(
        reading, "Старый и Новый замки", observed=set()
    )
    assert [r.name for r in requirements] == ["Старый"]

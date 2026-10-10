"""A territory is not a stop."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.mapping import _reading_requirements
from agent.schema import AgentReading, AgentRequirement


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
    reading = _reading(
        AgentRequirement(kind="must_visit", strength="hard", name="Старый")
    )
    requirements, _ = _reading_requirements(
        reading, "Старый и Новый замки", observed=set()
    )
    assert [r.name for r in requirements] == ["Старый"]

"""The moment a walk is planned for: the wire, the run, the prompt, and the answer."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent.client import _deps
from agent.models import ReaderBrief
from agent.prompts.notes import request_note
from api.models.routes import GenerateReq
from api.responses import interpretation, reader_brief
from core.hours import parse_moment
from quality.bench.cases import _filter_moment
from quality.bench.compliance import _moment_check, _requirement_status_check
from routing.models import Requirement, TripRequirements

CHOSEN = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)


def test_a_wall_clock_on_the_wire_is_grodno_time() -> None:
    """«10:00» typed by a visitor means ten in the morning in Grodno, not ten UTC."""
    req = GenerateReq(query="музеи Гродно", at="2026-10-20T10:00")

    assert req.at == datetime(2026, 10, 20, 7, 0, tzinfo=UTC)


def test_an_instant_on_the_wire_is_taken_as_placed() -> None:
    placed = GenerateReq(query="музеи Гродно", at="2026-10-20T10:00+03:00")
    utc = GenerateReq(query="музеи Гродно", at="2026-10-20T07:00:00Z")

    assert placed.at == utc.at == datetime(2026, 10, 20, 7, 0, tzinfo=UTC)
    assert GenerateReq(query="музеи Гродно").at is None


@pytest.mark.parametrize("moment", ["2019-06-01T10:00", "2100-01-01T10:00"])
def test_a_moment_outside_the_years_the_planner_covers_is_refused(moment: str) -> None:
    with pytest.raises(ValidationError):
        GenerateReq(query="музеи Гродно", at=moment)


def test_the_run_reads_the_moment_the_visitor_chose() -> None:
    brief = reader_brief(GenerateReq(query="музеи Гродно", at="2026-10-20T10:00"))

    assert brief.at == datetime(2026, 10, 20, 7, 0, tzinfo=UTC)

    deps = _deps(brief, None, None)

    assert deps.now == brief.at
    assert deps.at_chosen is True


def test_without_a_chosen_moment_the_run_reads_its_own_clock() -> None:
    deps = _deps(ReaderBrief(), None, None)

    assert deps.at_chosen is False
    assert abs((datetime.now(UTC) - deps.now).total_seconds()) < 60


def test_the_prompt_names_the_moment_the_walk_is_planned_for() -> None:
    chosen = request_note(ReaderBrief(at=CHOSEN))

    assert "when=2026-10-20 11:00" in chosen
    assert "when=now" in request_note(ReaderBrief())


def test_the_answer_echoes_the_moment_only_when_it_was_chosen() -> None:
    chose = TripRequirements(at=CHOSEN, at_chosen=True)
    clock = TripRequirements(at=CHOSEN)

    assert interpretation(chose, "ready").at == CHOSEN
    assert interpretation(clock, "ready").at is None


def _echo(status: str, at: str | None = None) -> dict:
    signal = {"kind": "open", "status": status}
    body: dict = {"interpretation": {"requirements": [signal]}}
    if at is not None:
        body["interpretation"]["at"] = at
    return body


def test_a_case_can_demand_a_status_and_not_settle_for_uncertain() -> None:
    exp = {"requirement_status": {"open": "satisfied"}}

    assert _requirement_status_check(_echo("satisfied"), exp)["ok"] is True
    assert _requirement_status_check(_echo("uncertain"), exp)["ok"] is False
    assert _requirement_status_check(_echo("unmet"), exp)["ok"] is False
    assert _requirement_status_check({"interpretation": {}}, exp)["ok"] is False


def test_a_case_measured_on_another_moment_is_a_failure() -> None:
    asked = "2026-10-20T11:00"

    assert _moment_check(_echo("satisfied", "2026-10-20T08:00+00:00"), asked)["ok"] is True
    assert _moment_check(_echo("satisfied", "2026-10-20T21:00+03:00"), asked)["ok"] is False
    assert _moment_check(_echo("satisfied"), asked)["ok"] is False
    assert _moment_check(_echo("satisfied", "2026-10-20T11:00+00:00"), asked)["ok"] is False


def test_a_case_moment_has_to_be_a_moment() -> None:
    assert _filter_moment({}, "case") == []
    assert _filter_moment({"at": "2026-10-20T11:00"}, "case") == []
    assert _filter_moment({"at": "вечером"}, "case")
    assert _filter_moment({"at": 20261020}, "case")


def test_the_moment_reaches_the_open_verdict() -> None:
    """The same stop is open at a chosen morning hour and closed at a chosen night one."""
    stops = {
        "route": [
            {"id": 1, "name": "Музей", "category": "музей", "opening_hours": "Mo-Su 09:00-18:00"}
        ],
        "trace": {},
    }
    shape = {"type": "LineString", "coordinates": [[23.83, 53.68], [23.84, 53.69]]}

    from routing.models import CategoryFacts
    from routing.verify import verify

    verdicts = {}
    for moment in ("2026-10-20T10:00", "2026-10-20T21:00"):
        at = parse_moment(moment)
        contract = TripRequirements(requirements=[Requirement(kind="open", strength="hard")], at=at)
        verify(contract, stops, shape, facts=CategoryFacts(), at=at)
        verdicts[moment] = contract.requirements[0].status

    assert verdicts == {"2026-10-20T10:00": "satisfied", "2026-10-20T21:00": "unmet"}

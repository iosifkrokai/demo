"""The verdict is the code's, not the model's — the property this design rests on."""

from __future__ import annotations

OLD_TOWN = (53.6778, 23.8295)
SCAN = 200


def _stops(repos, limit: int = 5) -> list:
    """Real rows near one another that can hold a walk — services are not stops.

    The nearest rows to the old town are mostly cafés and bus stops, so the scan is
    wide and the services are dropped, exactly as the planner drops them.
    """
    from routing.models import CategoryFacts

    facts = CategoryFacts.from_rows(repos.taxonomy.all())
    found = repos.places.nearby(OLD_TOWN[0], OLD_TOWN[1], radius_km=1.5, limit=SCAN)
    rows = [row for row in found if row.id is not None and not facts.is_service(row.category)]
    return rows[:limit]


def test_a_present_stop_satisfies_a_must_visit(db_repos) -> None:
    from agent.tools.plan import plan_walk
    from agent.tools.verify import check_requirements
    from routing.models import WalkRequest

    stops = _stops(db_repos, 4)
    ids = [row.id for row in stops]
    shape = plan_walk(WalkRequest(ids, budget_minutes=180), repos=db_repos)["shape"]
    first = stops[0]
    answer = check_requirements(
        [{"kind": "must_visit", "strength": "hard", "name": first.name, "place_id": first.id}],
        ids,
        repos=db_repos,
        facts=_facts(db_repos),
        geometry=shape,
    )

    assert answer.get("error") is None, answer.get("message")
    assert answer["verdicts"][0]["status"] == "satisfied"


def test_without_a_drawn_line_a_visit_is_uncertain_not_claimed(db_repos) -> None:
    """The verifier never claims a stop was visited on the strength of an id."""
    from agent.tools.verify import check_requirements

    stops = _stops(db_repos, 4)
    first = stops[0]
    answer = check_requirements(
        [{"kind": "must_visit", "strength": "hard", "name": first.name, "place_id": first.id}],
        [row.id for row in stops],
        repos=db_repos,
        facts=_facts(db_repos),
    )

    assert answer["verdicts"][0]["status"] == "uncertain"
    assert answer["status"] != "ready"


def test_an_absent_place_is_unmet_not_ignored(db_repos) -> None:
    from agent.tools.verify import check_requirements

    stops = _stops(db_repos, 4)
    answer = check_requirements(
        [{"kind": "must_visit", "strength": "hard", "name": "Того, чего нет", "place_id": None}],
        [row.id for row in stops],
        repos=db_repos,
        facts=_facts(db_repos),
    )

    assert answer["verdicts"][0]["status"] in ("unmet", "uncertain")
    assert answer["status"] != "ready"


def test_submit_measures_and_verifies_before_it_answers(db_repos) -> None:
    from agent.schema import PlanSubmission
    from agent.tools.submit import measure_submission
    from agent.tools.verify import requirements_from
    from routing.models import WalkRequest

    stops = _stops(db_repos, 4)
    ids = [row.id for row in stops]
    answer = measure_submission(
        WalkRequest(ids, budget_minutes=180),
        PlanSubmission(),
        repos=db_repos,
        facts=_facts(db_repos),
        contract=requirements_from(
            [
                {
                    "kind": "must_visit",
                    "strength": "hard",
                    "name": stops[0].name,
                    "place_id": stops[0].id,
                }
            ]
        ),
    )

    assert answer.get("error") is None, answer.get("message")
    assert answer["status"] in ("ready", "degraded", "infeasible", "pending")
    assert answer["ok"] is (answer["status"] == "ready")
    assert answer["problems"] == [] if answer["ok"] else answer["problems"]
    assert answer["total_minutes"] >= 0


def test_submit_refuses_to_call_an_impossible_plan_ready(db_repos) -> None:
    from agent.schema import PlanSubmission
    from agent.tools.submit import measure_submission
    from agent.tools.verify import requirements_from
    from routing.models import WalkRequest

    stops = _stops(db_repos, 4)
    answer = measure_submission(
        WalkRequest([row.id for row in stops], budget_minutes=180),
        PlanSubmission(),
        repos=db_repos,
        facts=_facts(db_repos),
        contract=requirements_from(
            [{"kind": "must_visit", "strength": "hard", "name": "Несуществующее место"}]
        ),
    )

    assert answer.get("error") is None, answer.get("message")
    assert answer["ok"] is False
    assert answer["problems"], "an unprovable hard requirement must be named"
    assert any(p["kind"] == "must_visit" for p in answer["problems"])


def _facts(repos):
    from routing.models import CategoryFacts

    return CategoryFacts.from_rows(repos.taxonomy.all())


def test_a_catalogue_submits_the_places_themselves(db_repos) -> None:
    """The answer to «все костёлы Вороновского района» is places, not the verdicts about
    them: a response builder reads cards, and a verdict carries no id to be read.

    The district is named because the catalogue reads a bounded pool per code: a whole
    oblast holds more костёлы than the pool does, and then the list is a slice of them.
    """
    from agent.schema import PlanSubmission
    from agent.tools.submit import measure_submission
    from routing.models import Requirement, TripRequirements, WalkRequest

    rows = [
        row
        for row in db_repos.places.with_category(["костёл"])
        if row.id is not None and row.district == "Вороновский район"
    ]
    assert len(rows) >= 2, "the catalogue needs костёлы to hold"
    ids = [row.id for row in rows]
    contract = TripRequirements(
        result_mode="catalogue",
        areas=["voronovo-district"],
        requirements=[Requirement(kind="interest", code="костёл")],
    )

    answer = measure_submission(
        WalkRequest(ids),
        PlanSubmission(ordered_ids=ids),
        repos=db_repos,
        facts=_facts(db_repos),
        contract=contract,
    )

    cards = answer["results"]
    assert all(card["id"] for card in cards), "every entry is a place with an id"
    assert set(ids) <= {card["id"] for card in cards}, "the agent's own list is inside it"
    assert {card["category"] for card in cards} == {"костёл"}
    assert answer["verdicts"], "the verdicts still ride along at the top level"
    assert answer.get("error") is None


def test_a_catalogue_left_empty_by_the_agent_is_built_by_the_code(db_repos) -> None:
    """The list is the request's, not the model's memory: an area and a code are
    enough to answer «все костёлы области», so nobody has to enumerate them."""
    from agent.schema import PlanSubmission
    from agent.tools.submit import measure_submission
    from routing.models import Requirement, TripRequirements, WalkRequest

    contract = TripRequirements(
        result_mode="catalogue",
        areas=["grodno-oblast"],
        requirements=[Requirement(kind="interest", code="костёл")],
    )

    answer = measure_submission(
        WalkRequest([]),
        PlanSubmission(),
        repos=db_repos,
        facts=_facts(db_repos),
        contract=contract,
    )

    assert len(answer["results"]) >= 10
    assert {card["category"] for card in answer["results"]} == {"костёл"}
    assert all(card["id"] for card in answer["results"])

"""The verdict is the code's, not the model's — the property this design rests on.

`submit_plan` is the only way a run ends, and it never takes the model's word for
whether its own work is good: it re-measures and re-verifies. These hold that
`ok` follows the code, and that a plan which cannot satisfy a hard requirement
comes back saying so instead of saying it is fine.
"""

from __future__ import annotations

OLD_TOWN = (53.6778, 23.8295)


def _stops(repos, limit: int = 5) -> list:
    found = repos.places.nearby(OLD_TOWN[0], OLD_TOWN[1], radius_km=1.5, limit=limit)
    return [row for row in found if row.id is not None]


def test_a_present_stop_satisfies_a_must_visit(db_repos) -> None:
    from agent.tools.plan import plan_walk
    from agent.tools.verify import check_requirements

    stops = _stops(db_repos, 4)
    ids = [row.id for row in stops]
    # The drawn line is the evidence that a visit happened; without it the
    # verifier deliberately refuses to call a stop satisfied.
    shape = plan_walk(ids, repos=db_repos, budget_minutes=180)["shape"]
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
    from agent.tools.submit import submit_plan

    stops = _stops(db_repos, 4)
    ids = [row.id for row in stops]
    answer = submit_plan(
        ids,
        repos=db_repos,
        facts=_facts(db_repos),
        budget_minutes=180,
        requirement_specs=[
            {
                "kind": "must_visit",
                "strength": "hard",
                "name": stops[0].name,
                "place_id": stops[0].id,
            }
        ],
    )

    assert answer.get("error") is None, answer.get("message")
    # The status is code's, and `ok` is derived from it rather than asserted.
    assert answer["status"] in ("ready", "degraded", "infeasible", "pending")
    assert answer["ok"] is (answer["status"] == "ready")
    assert answer["problems"] == [] if answer["ok"] else answer["problems"]
    assert answer["total_minutes"] >= 0


def test_submit_refuses_to_call_an_impossible_plan_ready(db_repos) -> None:
    from agent.tools.submit import submit_plan

    stops = _stops(db_repos, 4)
    answer = submit_plan(
        [row.id for row in stops],
        repos=db_repos,
        facts=_facts(db_repos),
        budget_minutes=15,
        requirement_specs=[
            {"kind": "must_visit", "strength": "hard", "name": "Несуществующее место"}
        ],
    )

    assert answer.get("error") is None, answer.get("message")
    assert answer["ok"] is False
    assert answer["problems"], "an unprovable hard requirement must be named"
    assert any(p["kind"] == "must_visit" for p in answer["problems"])


def _facts(repos):
    from routing.models import CategoryFacts

    return CategoryFacts.from_rows(repos.taxonomy.all())

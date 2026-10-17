"""The route the client is holding, measured over again without a model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.models.routes import RerouteReq, RerouteStop
from api.responses import reroute_response

OLD_TOWN = (53.6778, 23.8295)
SCAN = 200


def _stops(repos, count: int = 4) -> list[RerouteStop]:
    """Hand-picked stops near one another, taken from the catalogue, not a model.

    Sights only: the planner does not stop at a service, and the nearest rows are mostly them."""
    from routing.models import CategoryFacts

    facts = CategoryFacts.from_rows(repos.taxonomy.all())
    found = repos.places.nearby(OLD_TOWN[0], OLD_TOWN[1], radius_km=1.5, limit=SCAN)
    rows = [row for row in found if row.id is not None and not facts.is_service(row.category)]
    if len(rows) < count:
        pytest.skip("not enough sights in the dataset to build a walk")
    return [RerouteStop(key=f"w{index}", place_id=row.id) for index, row in enumerate(rows[:count])]


def _keys(response) -> list[str]:
    return [point.key or "" for point in response.points]


def test_the_walk_the_client_sent_is_measured(db_repos) -> None:
    req = RerouteReq(stops=_stops(db_repos))

    answer = reroute_response(req, repos=db_repos)

    assert len(answer.points) >= 2
    assert answer.shape, "a measured walk has a line"
    assert answer.budget is not None


def test_the_numbers_the_panel_shows_add_up(db_repos) -> None:
    """The walk, the visits and the total are one currency: a panel that renders a
    sum has to render a sum that comes out."""
    req = RerouteReq(stops=_stops(db_repos))

    budget = reroute_response(req, repos=db_repos).budget

    assert budget is not None
    assert budget.walk_minutes + budget.visit_minutes == budget.total_minutes
    assert budget.visit_minutes > 0


def test_the_summary_and_the_budget_speak_of_the_same_walk(db_repos) -> None:
    """Two figures for one walk is how a route came to read "121 walking, 692 total"."""
    req = RerouteReq(stops=_stops(db_repos))

    answer = reroute_response(req, repos=db_repos)

    assert answer.budget is not None
    assert answer.summary.time_seconds == budget_seconds(answer)


def budget_seconds(answer) -> float:
    assert answer.budget is not None
    return float(answer.budget.walk_minutes) * 60


def test_an_order_the_client_set_is_not_touched(db_repos) -> None:
    """The client's own order is the answer: it dragged those stops there."""
    stops = _stops(db_repos)
    req = RerouteReq(stops=stops, preserve_order=True)

    answer = reroute_response(req, repos=db_repos)

    assert _keys(answer) == [stop.key for stop in stops]


def test_an_order_the_client_left_open_is_ordered_for_them(db_repos) -> None:
    stops = _stops(db_repos)
    req = RerouteReq(stops=stops)

    answer = reroute_response(req, repos=db_repos)

    assert sorted(_keys(answer)) == sorted(stop.key for stop in stops)


def test_a_point_the_client_placed_itself_is_measured_too(db_repos) -> None:
    """A spot dropped on the map has no row and no id — it is still a stop."""
    stops = [
        *_stops(db_repos, 3),
        RerouteStop(key="dropped", lat=OLD_TOWN[0], lon=OLD_TOWN[1], name="Точка на карте"),
    ]
    req = RerouteReq(stops=stops, preserve_order=True)

    answer = reroute_response(req, repos=db_repos)

    dropped = next(point for point in answer.points if point.key == "dropped")
    assert (dropped.lat, dropped.lon) == pytest.approx(OLD_TOWN)
    assert dropped.name == "Точка на карте"
    assert dropped.id < 0, "the catalogue numbers from one upwards"


def test_a_service_the_client_placed_is_one_of_its_stops(db_repos) -> None:
    """The panel's points are the client's own doing: a café dragged onto the map
    stays on it, even though the planner would never have chosen one."""
    from routing.models import CategoryFacts

    facts = CategoryFacts.from_rows(db_repos.taxonomy.all())
    nearby = db_repos.places.nearby(OLD_TOWN[0], OLD_TOWN[1], radius_km=1.5, limit=SCAN)
    service = next(
        (row for row in nearby if row.id is not None and facts.is_service(row.category)), None
    )
    if service is None:
        pytest.skip("no service near the old town")

    stops = [*_stops(db_repos, 3), RerouteStop(key="cafe", place_id=service.id)]

    answer = reroute_response(RerouteReq(stops=stops, preserve_order=True), repos=db_repos)

    assert "cafe" in _keys(answer), answer.points


def test_the_tourists_own_position_is_the_walk_the_budget_counts(db_repos) -> None:
    """Where the tourist is standing is a leg of the walk, so it belongs to the
    measurement and not only to the line that gets drawn."""
    stops = _stops(db_repos)
    without = reroute_response(RerouteReq(stops=stops), repos=db_repos)
    with_origin = reroute_response(
        RerouteReq(stops=stops, origin={"lat": OLD_TOWN[0], "lon": OLD_TOWN[1]}), repos=db_repos
    )

    assert without.budget is not None
    assert with_origin.budget is not None
    assert with_origin.budget.total_minutes > without.budget.total_minutes


def test_a_stop_with_nowhere_to_be_is_refused() -> None:
    with pytest.raises(ValidationError):
        RerouteStop(key="nowhere")


def test_a_walk_needs_two_stops() -> None:
    with pytest.raises(ValidationError):
        RerouteReq(stops=[RerouteStop(key="a", place_id=7)])


def test_the_matrix_agrees_with_the_line_that_gets_drawn(db_repos) -> None:
    """The matrix is the currency of every rule, and the drawn line is what the tourist walks.

    When the two disagree, the rules are decided on numbers nobody will ever walk."""
    from routing.render import render
    from routing.stops import candidate_of
    from routing.valhalla.matrix import time_matrix

    rows = db_repos.places.get_by_ids(
        [stop.place_id for stop in _stops(db_repos, 2) if stop.place_id is not None]
    )
    assert len(rows) == 2
    coords = [{"lat": row.lat, "lon": row.lon} for row in rows]

    matrix = time_matrix(coords, coords, "pedestrian")
    _, summary, status = render([candidate_of(row, 0.0) for row in rows], costing="pedestrian")

    assert status == "usable"
    assert matrix[0][1] == pytest.approx(summary["time"], rel=0.1)

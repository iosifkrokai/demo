"""One success case per HTTP endpoint, against the running stack."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from agent.runner import WALL_CLOCK_TIMEOUT_S
from api.models.accounts import (
    AdminPlaceListOut,
    AdminUserListOut,
    AuthMeOut,
    PlaceItem,
    PublicUser,
    StatsOut,
    VisitedBulkOut,
    VisitedItem,
    VisitedListOut,
)
from api.models.routes import HealthResponse, RouteProgressOut, RouteResponse


def _carries(payload: dict[str, Any], model: type[BaseModel]) -> None:
    """The response carries at least every field the wire model declares."""
    missing = set(model.model_fields) - payload.keys()
    assert not missing, f"{model.__name__} is missing {sorted(missing)}"


@pytest.fixture
def api(live_stack) -> Iterator[httpx.Client]:
    """An HTTP client bound to the live stack; cookies persist across calls."""
    timeout = httpx.Timeout(connect=15.0, read=2 * WALL_CLOCK_TIMEOUT_S, write=30.0, pool=30.0)
    with httpx.Client(base_url=live_stack.url, timeout=timeout) as client:
        yield client


@pytest.fixture
def account(api: httpx.Client) -> str:
    """Register a throwaway user; ``api`` is left holding the session cookie."""
    email = f"test-{uuid.uuid4().hex}@example.com"
    response = api.post("/auth/register", json={"email": email, "password": "correct-horse-9"})
    assert response.status_code == 201, response.text
    return email


@pytest.fixture
def admin(api: httpx.Client) -> httpx.Client:
    """Log in as the administrator named by the environment, or skip."""
    email = os.environ.get("GRODNO_ADMIN_EMAIL")
    password = os.environ.get("GRODNO_ADMIN_PASSWORD")
    if not email or not password:
        pytest.skip("set GRODNO_ADMIN_EMAIL / GRODNO_ADMIN_PASSWORD to cover /admin")
    response = api.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return api


@pytest.fixture
def created_place(admin: httpx.Client) -> Iterator[dict[str, Any]]:
    """A brand-new place, removed again on the way out."""
    response = admin.post(
        "/admin/places",
        json={
            "name": "Тестовая точка",
            "lat": 53.68,
            "lon": 23.83,
            "source_url": f"test:{uuid.uuid4().hex}",
        },
    )
    assert response.status_code == 201, response.text
    place = response.json()
    yield place
    admin.delete(f"/admin/places/{place['place_id']}")


def _place_ids(api: httpx.Client, count: int) -> list[int]:
    """The first `count` real place ids, taken from the catalogue itself."""
    items = api.get("/places").json()["items"]
    assert len(items) >= count, f"the catalogue has fewer than {count} places"
    return [item["place_id"] for item in items[:count]]


def test_health_reports_its_dependencies(api: httpx.Client) -> None:
    response = api.get("/health")
    assert response.status_code == 200, response.text
    _carries(response.json(), HealthResponse)
    assert response.json()["status"] in {"ok", "degraded"}


def test_generate_returns_a_route(live_stack, api: httpx.Client) -> None:
    live_stack.require("llm")
    progress_id = uuid.uuid4().hex
    response = api.post(
        "/routes/generate",
        json={"query": "погулять по старому Гродно", "progress_id": progress_id},
    )
    assert response.status_code == 200, response.text
    _carries(response.json(), RouteResponse)

    tracked = api.get(f"/routes/progress/{progress_id}")
    assert tracked.status_code == 200, tracked.text
    _carries(tracked.json(), RouteProgressOut)
    assert tracked.json()["done"] is True


def test_progress_for_an_untracked_run_is_a_404(api: httpx.Client) -> None:
    response = api.get(f"/routes/progress/{uuid.uuid4().hex}")

    assert response.status_code == 404
    assert response.json() == {"reason": "unknown_progress_id"}


def test_reroute_returns_a_route(api: httpx.Client) -> None:
    """A client editing its own route must not need an interpreter for it, so this
    asks for no model (no ``live_stack.require("llm")``) and skips nothing."""
    ids = _place_ids(api, 2)
    stops = [{"key": str(index), "place_id": place_id} for index, place_id in enumerate(ids)]
    response = api.post("/routes/reroute", json={"stops": stops})
    assert response.status_code == 200, response.text
    _carries(response.json(), RouteResponse)
    budget = response.json()["budget"]
    assert budget["walk_minutes"] + budget["visit_minutes"] == budget["total_minutes"]


def test_reroute_measures_the_order_the_client_sent(api: httpx.Client) -> None:
    ids = _place_ids(api, 3)
    stops = [{"key": f"w{index}", "place_id": place_id} for index, place_id in enumerate(ids)]
    response = api.post(
        "/routes/reroute", json={"stops": stops, "preserve_order": True, "profile": "pedestrian"}
    )
    assert response.status_code == 200, response.text
    assert [point["key"] for point in response.json()["points"]] == ["w0", "w1", "w2"]
    assert [point["id"] for point in response.json()["points"]] == ids


def test_explain_returns_an_explanation(live_stack, api: httpx.Client) -> None:
    live_stack.require("llm")
    response = api.post("/routes/explain", json={"point_ids": _place_ids(api, 2)})
    assert response.status_code == 200, response.text
    assert isinstance(response.json()["explanation"], str)


def test_places_lists_the_catalogue(api: httpx.Client) -> None:
    response = api.get("/places")
    assert response.status_code == 200, response.text
    assert isinstance(response.json()["items"], list)


def test_itineraries_lists_ready_made_routes(api: httpx.Client) -> None:
    response = api.get("/routes/itineraries")
    assert response.status_code == 200, response.text
    assert {"items", "missing"} <= response.json().keys()


def test_services_along_a_line(api: httpx.Client) -> None:
    response = api.post(
        "/routes/services",
        json={"shape": {"type": "LineString", "coordinates": [[23.83, 53.68], [23.84, 53.68]]}},
    )
    assert response.status_code == 200, response.text
    assert {"items", "capped"} <= response.json().keys()


def test_register_creates_and_signs_in(api: httpx.Client) -> None:
    email = f"test-{uuid.uuid4().hex}@example.com"
    response = api.post("/auth/register", json={"email": email, "password": "correct-horse-9"})
    assert response.status_code == 201, response.text
    _carries(response.json(), PublicUser)
    assert response.json()["role"] == "user"


def test_login_returns_the_user(api: httpx.Client, account: str) -> None:
    response = api.post("/auth/login", json={"email": account, "password": "correct-horse-9"})
    assert response.status_code == 200, response.text
    _carries(response.json(), PublicUser)
    assert response.json()["email"] == account


def test_logout_ends_the_session(api: httpx.Client, account: str) -> None:
    response = api.post("/auth/logout")
    assert response.status_code == 204, response.text


def test_me_is_honest_when_anonymous(api: httpx.Client) -> None:
    response = api.get("/auth/me")
    assert response.status_code == 200, response.text
    _carries(response.json(), AuthMeOut)
    assert response.json()["authenticated"] is False


def test_visited_is_listed(api: httpx.Client, account: str) -> None:
    response = api.get("/me/visited")
    assert response.status_code == 200, response.text
    _carries(response.json(), VisitedListOut)


def test_a_place_is_marked_visited(api: httpx.Client, account: str) -> None:
    place_id = _place_ids(api, 1)[0]
    response = api.put(f"/me/visited/{place_id}")
    assert response.status_code == 200, response.text
    _carries(response.json(), VisitedItem)
    assert response.json()["place_id"] == place_id


def test_places_are_marked_visited_in_bulk(api: httpx.Client, account: str) -> None:
    place_id = _place_ids(api, 1)[0]
    response = api.post("/me/visited", json={"place_ids": [place_id]})
    assert response.status_code == 200, response.text
    _carries(response.json(), VisitedBulkOut)
    assert place_id in response.json()["marked"]


def test_a_place_is_unmarked_visited(api: httpx.Client, account: str) -> None:
    place_id = _place_ids(api, 1)[0]
    assert api.put(f"/me/visited/{place_id}").status_code == 200
    response = api.delete(f"/me/visited/{place_id}")
    assert response.status_code == 204, response.text


def test_admin_users_are_listed(admin: httpx.Client) -> None:
    response = admin.get("/admin/users")
    assert response.status_code == 200, response.text
    _carries(response.json(), AdminUserListOut)


def test_admin_renames_a_user(admin: httpx.Client) -> None:
    users = admin.get("/admin/users").json()["items"]
    assert users, "an administrator is at least one user"
    response = admin.patch(f"/admin/users/{users[0]['id']}", json={"display_name": "Тест"})
    assert response.status_code == 200, response.text
    _carries(response.json(), PublicUser)
    assert response.json()["display_name"] == "Тест"


def test_admin_deletes_a_user(admin: httpx.Client, live_stack) -> None:
    with httpx.Client(base_url=live_stack.url, timeout=30.0) as throwaway:
        email = f"throwaway-{uuid.uuid4().hex}@example.com"
        created = throwaway.post(
            "/auth/register", json={"email": email, "password": "correct-horse-9"}
        )
        assert created.status_code == 201, created.text
        user_id = created.json()["id"]
    response = admin.delete(f"/admin/users/{user_id}")
    assert response.status_code == 204, response.text


def test_admin_places_are_listed(admin: httpx.Client) -> None:
    response = admin.get("/admin/places")
    assert response.status_code == 200, response.text
    _carries(response.json(), AdminPlaceListOut)


def test_admin_creates_a_place(created_place: dict[str, Any]) -> None:
    _carries(created_place, PlaceItem)
    assert created_place["name"] == "Тестовая точка"


def test_admin_patches_a_place(admin: httpx.Client, created_place: dict[str, Any]) -> None:
    response = admin.patch(f"/admin/places/{created_place['place_id']}", json={"blurb": "Тест"})
    assert response.status_code == 200, response.text
    _carries(response.json(), PlaceItem)
    assert response.json()["blurb"] == "Тест"


def test_admin_deletes_a_place(admin: httpx.Client, created_place: dict[str, Any]) -> None:
    response = admin.delete(f"/admin/places/{created_place['place_id']}")
    assert response.status_code == 204, response.text


def test_admin_stats_are_reported(admin: httpx.Client) -> None:
    response = admin.get("/admin/stats")
    assert response.status_code == 200, response.text
    _carries(response.json(), StatsOut)

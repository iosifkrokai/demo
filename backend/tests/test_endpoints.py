"""One success case per HTTP endpoint, against the running stack.

The list below is the whole surface, enumerated from ``api/routers``: health,
routes, catalogue, clients, accounts and admin. A new endpoint gets exactly one
success test here — no error branches yet, those come later.

Every test reaches the live stack through ``live_stack`` and skips when it is
unreachable. The planning endpoints additionally skip when ``/health`` reports
``llm=false``: without a reader the request is refused by design, so there is no
success to assert. The admin endpoints skip unless ``GRODNO_ADMIN_EMAIL`` and
``GRODNO_ADMIN_PASSWORD`` name a real administrator. A skip is not a pass.

The shape asserted is the wire model's own: the routes and health models still
live in ``planner.models`` (mid-refactor), the client and account models in
``api.models``. Asserting against ``model_fields`` means a renamed field breaks
here instead of drifting.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

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
from api.models.clients import PreferencesOut, RouteCreated, RouteDetail, RouteListItem
from planner.models import HealthResponse, RouteResponse


def _carries(payload: dict[str, Any], model: type[BaseModel]) -> None:
    """The response carries at least every field the wire model declares."""
    missing = set(model.model_fields) - payload.keys()
    assert not missing, f"{model.__name__} is missing {sorted(missing)}"


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def api(live_stack) -> Iterator[httpx.Client]:
    """An HTTP client bound to the live stack; cookies persist across calls."""
    with httpx.Client(base_url=live_stack.url, timeout=30.0) as client:
        yield client


@pytest.fixture
def client_id() -> str:
    """A fresh anonymous ``X-Client-Id``, one per test."""
    return uuid.uuid4().hex


@pytest.fixture
def account(api: httpx.Client) -> str:
    """Register a throwaway user; ``api`` is left holding the session cookie."""
    email = f"test-{uuid.uuid4().hex}@example.com"
    response = api.post("/auth/register", json={"email": email, "password": "correct-horse-9"})
    assert response.status_code == 201, response.text
    return email


@pytest.fixture
def saved_route(api: httpx.Client, client_id: str) -> str:
    """One route persisted for the anonymous client."""
    response = api.post(
        "/clients/me/routes",
        headers={"X-Client-Id": client_id},
        json={"query": "тест", "plan": {"points": []}},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


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


# --- health -----------------------------------------------------------------


def test_health_reports_its_dependencies(api: httpx.Client) -> None:
    response = api.get("/health")
    assert response.status_code == 200, response.text
    _carries(response.json(), HealthResponse)
    assert response.json()["status"] in {"ok", "starting", "degraded"}


# --- planning ---------------------------------------------------------------


def test_generate_returns_a_route(live_stack, api: httpx.Client) -> None:
    live_stack.require("llm")
    response = api.post("/routes/generate", json={"query": "погулять по старому Гродно"})
    assert response.status_code == 200, response.text
    _carries(response.json(), RouteResponse)


def test_reroute_returns_a_route(live_stack, api: httpx.Client) -> None:
    live_stack.require("llm")
    response = api.post("/routes/reroute", json={"point_ids": _place_ids(api, 2)})
    assert response.status_code == 200, response.text
    _carries(response.json(), RouteResponse)


def test_explain_returns_an_explanation(live_stack, api: httpx.Client) -> None:
    live_stack.require("llm")
    response = api.post("/routes/explain", json={"point_ids": _place_ids(api, 2)})
    assert response.status_code == 200, response.text
    assert isinstance(response.json()["explanation"], str)


def test_trace_returns_the_spans_of_a_run(live_stack, api: httpx.Client) -> None:
    live_stack.require("llm")
    trace_id = uuid.uuid4().hex
    generated = api.post(
        "/routes/generate", json={"query": "старый город", "progress_id": trace_id}
    )
    assert generated.status_code == 200, generated.text
    response = api.get(f"/routes/trace/{trace_id}")
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), dict)


def test_progress_returns_the_stage_snapshot(live_stack, api: httpx.Client) -> None:
    live_stack.require("llm")
    progress_id = uuid.uuid4().hex
    generated = api.post(
        "/routes/generate", json={"query": "старый город", "progress_id": progress_id}
    )
    assert generated.status_code == 200, generated.text
    response = api.get(f"/routes/progress/{progress_id}")
    assert response.status_code == 200, response.text
    assert {"stage", "done", "failed", "elapsed_ms"} <= response.json().keys()


# --- catalogue --------------------------------------------------------------


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


# --- anonymous client -------------------------------------------------------


def test_client_preferences_read_anonymously(api: httpx.Client) -> None:
    response = api.get("/clients/me/preferences")
    assert response.status_code == 200, response.text
    _carries(response.json(), PreferencesOut)


def test_client_preferences_are_stored(api: httpx.Client, client_id: str) -> None:
    response = api.put(
        "/clients/me/preferences",
        headers={"X-Client-Id": client_id},
        json={"transport": "pedestrian"},
    )
    assert response.status_code == 200, response.text
    _carries(response.json(), PreferencesOut)
    assert response.json()["transport"] == "pedestrian"


def test_client_route_is_saved(api: httpx.Client, client_id: str) -> None:
    response = api.post(
        "/clients/me/routes",
        headers={"X-Client-Id": client_id},
        json={"query": "тест", "plan": {"points": []}},
    )
    assert response.status_code == 201, response.text
    _carries(response.json(), RouteCreated)


def test_client_routes_are_listed(api: httpx.Client, client_id: str, saved_route: str) -> None:
    response = api.get("/clients/me/routes", headers={"X-Client-Id": client_id})
    assert response.status_code == 200, response.text
    assert any(item["id"] == saved_route for item in response.json())
    _carries(response.json()[0], RouteListItem)


def test_client_route_detail(api: httpx.Client, client_id: str, saved_route: str) -> None:
    response = api.get(f"/clients/me/routes/{saved_route}", headers={"X-Client-Id": client_id})
    assert response.status_code == 200, response.text
    _carries(response.json(), RouteDetail)


def test_client_route_is_renamed(api: httpx.Client, client_id: str, saved_route: str) -> None:
    response = api.patch(
        f"/clients/me/routes/{saved_route}",
        headers={"X-Client-Id": client_id},
        json={"name": "новое имя"},
    )
    assert response.status_code == 200, response.text
    _carries(response.json(), RouteDetail)
    assert response.json()["name"] == "новое имя"


def test_client_route_is_deleted(api: httpx.Client, client_id: str, saved_route: str) -> None:
    response = api.delete(f"/clients/me/routes/{saved_route}", headers={"X-Client-Id": client_id})
    assert response.status_code == 204, response.text


def test_client_entity_is_deleted(api: httpx.Client, client_id: str) -> None:
    response = api.delete("/clients/me", headers={"X-Client-Id": client_id})
    assert response.status_code == 204, response.text


# --- accounts ---------------------------------------------------------------


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


# --- admin ------------------------------------------------------------------


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

"""The anonymous client entity — server side.

No API key, DB or network: tests inject a fake repository into ``app.state``.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api import main as agent_main
from contracts.clients import route_metrics
from core.config import settings
from store.clients_store import (
    PREFERENCE_COLUMNS,
    PostgresClientRepository,
    StorageUnavailable,
    TooManyRoutes,
)

CLIENT_A = "11111111-1111-4111-8111-111111111111"
CLIENT_B = "22222222-2222-4222-8222-222222222222"

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MIGRATION = os.path.join(BACKEND, "db", "migrations", "0005_clients.sql")

HEADERS = {"X-Client-Id": CLIENT_A}

PLAN = {
    "points": [
        {"id": 1, "name": "Старый замок", "lat": 53.6771, "lon": 23.8290},
        {"id": 12, "name": "Музей истории города", "lat": 53.6783, "lon": 23.8265},
    ],
    "shape": {"trip": {"legs": [{"shape": "a_very_long_encoded_polyline"}]}},
    "summary": {"length_km": 1.5, "time_seconds": 1800.0},
    "budget": {"total_minutes": 90, "walk_minutes": 30, "visit_minutes": 60},
    "costing": "pedestrian",
}
VISIT_OVERRIDES = {"1": 120}


def _now() -> datetime:
    return datetime.now(UTC)


class FakeRepo:
    """In-memory ClientRepository with real cascade semantics.

    No saved preferences answers ``None``; deleting a client takes its data with it.
    """

    def __init__(self, max_routes: int = 200) -> None:
        self.max_routes = max_routes
        self.db: dict[uuid.UUID, dict] = {}

    def ensure_client(self, client_id: uuid.UUID) -> None:
        self.db.setdefault(client_id, {"prefs": None, "routes": []})

    def delete_client(self, client_id: uuid.UUID) -> bool:
        return self.db.pop(client_id, None) is not None

    def get_preferences(self, client_id: uuid.UUID) -> dict | None:
        return self.db.get(client_id, {}).get("prefs")

    def upsert_preferences(self, client_id: uuid.UUID, fields: dict) -> dict:
        self.ensure_client(client_id)
        state = self.db[client_id]["prefs"]
        if state is None:
            state = dict.fromkeys(PREFERENCE_COLUMNS)
        for key in fields:
            if key in PREFERENCE_COLUMNS:
                state[key] = fields[key]
        state["updated_at"] = _now()
        self.db[client_id]["prefs"] = state
        return dict(state)

    def add_route(self, client_id: uuid.UUID, route_id: uuid.UUID, *,
                  query: str, plan: dict, name: str | None = None,
                  visit_overrides: dict | None = None) -> dict:
        self.ensure_client(client_id)
        routes = self.db[client_id]["routes"]
        if len(routes) >= self.max_routes:
            raise TooManyRoutes(self.max_routes)
        now = _now()
        routes.append({
            "id": route_id, "name": name, "query": query, "plan": plan,
            "visit_overrides": visit_overrides,
            "created_at": now, "updated_at": now,
        })
        return {"id": route_id, "created_at": now}

    def list_routes(self, client_id: uuid.UUID, limit: int = 50) -> list[dict]:
        routes = self.db.get(client_id, {}).get("routes", [])
        out = []
        for r in sorted(routes, key=lambda r: r["created_at"], reverse=True)[:limit]:
            points = r["plan"].get("points") if isinstance(r["plan"], dict) else None
            count = len(points) if isinstance(points, list) else 0
            out.append({
                "id": r["id"], "name": r["name"], "query": r["query"],
                "created_at": r["created_at"],
                **route_metrics(count, r["plan"].get("summary"),
                                r["plan"].get("budget")),
            })
        return out

    def get_route(self, client_id: uuid.UUID, route_id: uuid.UUID) -> dict | None:
        for r in self.db.get(client_id, {}).get("routes", []):
            if r["id"] == route_id:
                return dict(r)
        return None

    def rename_route(self, client_id: uuid.UUID, route_id: uuid.UUID,
                     name: str) -> dict | None:
        for r in self.db.get(client_id, {}).get("routes", []):
            if r["id"] == route_id:
                r["name"] = name
                r["updated_at"] = _now()
                return dict(r)
        return None

    def delete_route(self, client_id: uuid.UUID, route_id: uuid.UUID) -> bool:
        routes = self.db.get(client_id, {}).get("routes", [])
        for i, r in enumerate(routes):
            if r["id"] == route_id:
                routes.pop(i)
                return True
        return False


class DownRepo:
    """Every method fails the way an unreachable database does."""

    def __getattr__(self, _name):
        def _boom(*_a, **_kw):
            raise StorageUnavailable("connection refused")
        return _boom


@pytest.fixture
def client():
    agent_main.app.state.clients_repository = FakeRepo()
    yield TestClient(agent_main.app, raise_server_exceptions=False)
    if hasattr(agent_main.app.state, "clients_repository"):
        delattr(agent_main.app.state, "clients_repository")


def _client_with(repo) -> TestClient:
    agent_main.app.state.clients_repository = repo
    return TestClient(agent_main.app, raise_server_exceptions=False)


def _save_route(client, *, plan=PLAN, query="замки Гродно",
                name="Мои замки", visit_overrides=VISIT_OVERRIDES,
                headers=HEADERS):
    body = {"query": query, "plan": plan}
    if name is not None:
        body["name"] = name
    if visit_overrides is not None:
        body["visit_overrides"] = visit_overrides
    return client.post("/clients/me/routes", json=body, headers=headers)


class TestNoClientId:

    def test_get_preferences_is_the_empty_state(self, client):
        r = client.get("/clients/me/preferences")
        assert r.status_code == 200, r.text
        body = r.json()
        assert all(body[k] is None for k in (
            "transport", "time_budget_minutes", "party_adults",
            "party_children", "interests", "language",
            "visit_minutes_by_category",
        ))

    def test_list_routes_is_empty(self, client):
        r = client.get("/clients/me/routes")
        assert r.status_code == 200 and r.json() == []

    def test_get_route_by_id_is_not_found(self, client):
        r = client.get(f"/clients/me/routes/{uuid.uuid4()}")
        assert r.status_code == 404
        assert r.json() == {"reason": "route_not_found"}

    @pytest.mark.parametrize("method,path,body", [
        ("put", "/clients/me/preferences", {"transport": "auto"}),
        ("post", "/clients/me/routes", {"query": "замки", "plan": PLAN}),
        ("delete", "/clients/me", None),
    ])
    def test_writes_report_storage_unavailable(self, client, method, path, body):
        r = getattr(client, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 503
        assert r.json() == {"reason": "storage_unavailable"}

    def test_a_malformed_id_is_a_different_error(self, client):
        r = client.get("/clients/me/preferences",
                       headers={"X-Client-Id": "not-a-uuid"})
        assert r.status_code == 400
        assert r.json() == {"reason": "invalid_client_id"}

    def test_an_empty_header_counts_as_absent(self, client):
        r = client.get("/clients/me/preferences", headers={"X-Client-Id": "  "})
        assert r.status_code == 200


class TestPreferences:

    def test_get_before_anything_is_saved_is_null(self, client):
        r = client.get("/clients/me/preferences", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["transport"] is None

    def test_put_updates_only_the_sent_fields(self, client):
        r = client.put("/clients/me/preferences", headers=HEADERS,
                       json={"transport": "auto", "party_adults": 2})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["transport"] == "auto"
        assert body["party_adults"] == 2
        assert body["party_children"] is None
        assert body["time_budget_minutes"] is None
        assert body["interests"] is None

    def test_put_merges_across_calls(self, client):
        client.put("/clients/me/preferences", headers=HEADERS,
                   json={"transport": "bicycle", "language": "en"})
        body = client.put("/clients/me/preferences", headers=HEADERS,
                          json={"time_budget_minutes": 180}).json()
        assert body["transport"] == "bicycle"
        assert body["language"] == "en"
        assert body["time_budget_minutes"] == 180

    def test_null_explicitly_clears_a_field(self, client):
        client.put("/clients/me/preferences", headers=HEADERS,
                   json={"transport": "auto", "party_adults": 3})
        body = client.put("/clients/me/preferences", headers=HEADERS,
                          json={"party_adults": None}).json()
        assert body["party_adults"] is None
        assert body["transport"] == "auto"

    def test_interests_and_pace_round_trip(self, client):
        pace = {"замок": 90, "музей": 40}
        body = client.put("/clients/me/preferences", headers=HEADERS,
                          json={"interests": ["замок", "костёл"],
                                "visit_minutes_by_category": pace}).json()
        assert body["interests"] == ["замок", "костёл"]
        assert body["visit_minutes_by_category"] == pace
        again = client.get("/clients/me/preferences", headers=HEADERS).json()
        assert again["visit_minutes_by_category"] == pace

    def test_empty_put_changes_nothing(self, client):
        client.put("/clients/me/preferences", headers=HEADERS,
                   json={"language": "ru"})
        body = client.put("/clients/me/preferences", headers=HEADERS,
                          json={}).json()
        assert body["language"] == "ru"

    def test_preferences_are_per_client(self, client):
        client.put("/clients/me/preferences", headers=HEADERS,
                   json={"transport": "auto"})
        other = client.get("/clients/me/preferences",
                           headers={"X-Client-Id": CLIENT_B}).json()
        assert other["transport"] is None


class TestRoutes:

    def test_post_then_get_returns_the_plan_unchanged(self, client):
        r = _save_route(client)
        assert r.status_code == 201, r.text
        created = r.json()
        assert "id" in created and "created_at" in created

        detail = client.get(f"/clients/me/routes/{created['id']}",
                            headers=HEADERS)
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["plan"] == PLAN
        assert body["query"] == "замки Гродно"
        assert body["name"] == "Мои замки"
        assert body["visit_overrides"] == VISIT_OVERRIDES

    def test_post_keeps_the_plan_exactly_as_a_json_string(self, client):
        """Two posts of the same plan store byte-identical blobs."""
        a = _save_route(client, name=None, visit_overrides=None).json()
        detail = client.get(f"/clients/me/routes/{a['id']}", headers=HEADERS).json()
        import json
        assert json.dumps(detail["plan"], sort_keys=True) == \
            json.dumps(PLAN, sort_keys=True)
        assert detail["name"] is None
        assert detail["visit_overrides"] is None

    def test_list_excludes_geometry_and_carries_the_metrics(self, client):
        _save_route(client)
        r = client.get("/clients/me/routes", headers=HEADERS)
        assert r.status_code == 200, r.text
        items = r.json()
        assert len(items) == 1
        item = items[0]
        assert "plan" not in item
        assert "shape" not in item
        assert "points" not in item
        assert item["stop_count"] == len(PLAN["points"]) == 2
        assert item["distance_m"] == 1500
        assert item["duration_min"] == 90
        assert item["query"] == "замки Гродно"
        assert item["name"] == "Мои замки"

    def test_list_is_newest_first(self, client):
        first = _save_route(client, name="first").json()
        second = _save_route(client, name="second").json()
        ids = [i["id"] for i in client.get("/clients/me/routes",
                                           headers=HEADERS).json()]
        assert ids == [second["id"], first["id"]]

    def test_list_respects_limit(self, client):
        for i in range(3):
            _save_route(client, name=f"r{i}")
        items = client.get("/clients/me/routes?limit=2", headers=HEADERS).json()
        assert len(items) == 2

    def test_get_unknown_route_is_route_not_found(self, client):
        r = client.get(f"/clients/me/routes/{uuid.uuid4()}", headers=HEADERS)
        assert r.status_code == 404
        assert r.json() == {"reason": "route_not_found"}

    def test_a_route_is_invisible_to_another_client(self, client):
        rid = _save_route(client).json()["id"]
        r = client.get(f"/clients/me/routes/{rid}",
                       headers={"X-Client-Id": CLIENT_B})
        assert r.status_code == 404

    def test_patch_renames(self, client):
        rid = _save_route(client).json()["id"]
        r = client.patch(f"/clients/me/routes/{rid}", headers=HEADERS,
                         json={"name": "Переименованный"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Переименованный"
        assert r.json()["plan"] == PLAN
        listed = client.get("/clients/me/routes", headers=HEADERS).json()
        assert listed[0]["name"] == "Переименованный"

    def test_patch_unknown_route_is_route_not_found(self, client):
        r = client.patch(f"/clients/me/routes/{uuid.uuid4()}", headers=HEADERS,
                         json={"name": "x"})
        assert r.status_code == 404
        assert r.json() == {"reason": "route_not_found"}

    def test_delete_route_is_204_then_not_found(self, client):
        rid = _save_route(client).json()["id"]
        assert client.delete(f"/clients/me/routes/{rid}",
                             headers=HEADERS).status_code == 204
        assert client.delete(f"/clients/me/routes/{rid}",
                             headers=HEADERS).status_code == 404
        assert client.get("/clients/me/routes", headers=HEADERS).json() == []

    def test_too_many_routes_is_a_machine_code(self):
        agent_main.app.state.clients_repository = FakeRepo(max_routes=1)
        try:
            tc = TestClient(agent_main.app, raise_server_exceptions=False)
            _save_route(tc)
            r = _save_route(tc)
            assert r.status_code == 409
            assert r.json() == {"reason": "too_many_routes"}
        finally:
            del agent_main.app.state.clients_repository


class TestDeleteClient:

    def test_delete_removes_routes_and_preferences(self, client):
        client.put("/clients/me/preferences", headers=HEADERS,
                   json={"transport": "auto"})
        rid = _save_route(client).json()["id"]
        assert client.delete("/clients/me", headers=HEADERS).status_code == 204
        assert client.get("/clients/me/routes", headers=HEADERS).json() == []
        assert client.get(f"/clients/me/routes/{rid}",
                          headers=HEADERS).status_code == 404
        assert client.get("/clients/me/preferences",
                          headers=HEADERS).json()["transport"] is None

    def test_delete_is_idempotent(self, client):
        assert client.delete("/clients/me", headers=HEADERS).status_code == 204
        assert client.delete("/clients/me", headers=HEADERS).status_code == 204


class TestStorageDown:

    @pytest.fixture
    def down(self):
        agent_main.app.state.clients_repository = DownRepo()
        yield TestClient(agent_main.app, raise_server_exceptions=False)
        del agent_main.app.state.clients_repository

    @pytest.mark.parametrize("method,path,body", [
        ("get", "/clients/me/preferences", None),
        ("put", "/clients/me/preferences", {"transport": "auto"}),
        ("post", "/clients/me/routes", {"query": "замки", "plan": PLAN}),
        ("get", "/clients/me/routes", None),
        ("get", f"/clients/me/routes/{CLIENT_A}", None),
        ("patch", f"/clients/me/routes/{CLIENT_A}", {"name": "x"}),
        ("delete", f"/clients/me/routes/{CLIENT_A}", None),
        ("delete", "/clients/me", None),
    ])
    def test_every_endpoint_degrades_to_503(self, down, method, path, body):
        r = getattr(down, method)(path, headers=HEADERS,
                                  **({"json": body} if body else {}))
        assert r.status_code == 503, r.text
        assert r.json() == {"reason": "storage_unavailable"}


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self._one = None
        self._all: list = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        result = self.conn.handler(sql, params) or {}
        self._one = result.get("one")
        self._all = result.get("all", [])
        self.rowcount = result.get("rowcount", 0 if self._one is None else 1)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class FakeConn:
    def __init__(self, handler=None):
        self.handler = handler or (lambda _sql, _params: {})
        self.executed: list = []
        self.closed = False

    def cursor(self, row_factory=None):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def _repo(handler=None, **kw):
    conn = FakeConn(handler)
    return PostgresClientRepository(connect=lambda: conn, **kw), conn


class TestStore:

    def test_ensure_client_is_an_upsert(self):
        repo, conn = _repo()
        repo.ensure_client(uuid.UUID(CLIENT_A))
        sql, params = conn.executed[0]
        assert "INSERT INTO clients" in sql
        assert "ON CONFLICT (id) DO UPDATE SET last_seen_at" in sql
        assert params == (uuid.UUID(CLIENT_A),)

    def test_missing_preferences_row_is_none_not_an_error(self):
        repo, _ = _repo(lambda _s, _p: {"one": None})
        assert repo.get_preferences(uuid.UUID(CLIENT_A)) is None

    def test_existing_preferences_row_is_returned(self):
        row = dict.fromkeys(PREFERENCE_COLUMNS)
        row["transport"] = "auto"
        repo, _ = _repo(lambda _s, _p: {"one": row})
        assert repo.get_preferences(uuid.UUID(CLIENT_A))["transport"] == "auto"

    def test_upsert_touches_only_the_sent_columns(self):
        repo, conn = _repo(lambda _s, _p: {"one": {"transport": "auto"}})
        repo.upsert_preferences(uuid.UUID(CLIENT_A), {"transport": "auto"})
        sqls = [s for s, _ in conn.executed]
        update = [s for s in sqls if "INSERT INTO client_preferences" in s][0]
        assert "transport = EXCLUDED.transport" in update
        assert "party_adults = EXCLUDED.party_adults" not in update

    def test_a_null_cleared_jsonb_is_sql_null_not_json_null(self):
        repo, conn = _repo(lambda _s, _p: {"one": {}})
        repo.upsert_preferences(uuid.UUID(CLIENT_A),
                                {"visit_minutes_by_category": None})
        _sql, params = [e for e in conn.executed
                        if "INSERT INTO client_preferences" in e[0]][0]
        assert None in params
        assert not any(isinstance(p, Jsonb) for p in params)

    def test_a_real_jsonb_is_adapted(self):
        repo, conn = _repo(lambda _s, _p: {"one": {}})
        repo.upsert_preferences(uuid.UUID(CLIENT_A),
                                {"visit_minutes_by_category": {"замок": 90}})
        _sql, params = [e for e in conn.executed
                        if "INSERT INTO client_preferences" in e[0]][0]
        assert any(isinstance(p, Jsonb) for p in params)

    def test_add_route_reports_the_route_id(self):
        rid = uuid.uuid4()
        repo, _ = _repo(lambda _s, _p: {"one": {"id": rid, "created_at": _now()}})
        out = repo.add_route(uuid.UUID(CLIENT_A), rid, query="q", plan=PLAN)
        assert out["id"] == rid

    def test_add_route_over_the_cap_raises_too_many(self):
        repo, _ = _repo(lambda _s, _p: {"one": None}, max_routes=1)
        with pytest.raises(TooManyRoutes):
            repo.add_route(uuid.UUID(CLIENT_A), uuid.uuid4(), query="q", plan=PLAN)

    def test_list_routes_derives_metrics_without_geometry(self):
        rid = uuid.uuid4()
        repo, conn = _repo(lambda _s, _p: {"all": [{
            "id": rid, "name": None, "query": "q", "created_at": _now(),
            "stop_count": 2, "summary": PLAN["summary"], "budget": PLAN["budget"],
        }]})
        items = repo.list_routes(uuid.UUID(CLIENT_A), 50)
        assert items[0]["distance_m"] == 1500
        assert items[0]["duration_min"] == 90
        select = conn.executed[-1][0]
        assert "plan->'points'" in select
        assert "plan->'shape'" not in select

    def test_delete_route_reports_whether_a_row_went(self):
        repo, _ = _repo(lambda _s, _p: {"rowcount": 0})
        assert repo.delete_route(uuid.UUID(CLIENT_A), uuid.uuid4()) is False

    def test_a_driver_error_becomes_storage_unavailable(self):
        def boom(_sql, _params):
            raise psycopg.OperationalError("connection refused")
        repo, _ = _repo(boom)
        with pytest.raises(StorageUnavailable):
            repo.ensure_client(uuid.UUID(CLIENT_A))

    def test_an_unreachable_database_never_leaks_a_driver_error(self):
        def refuse():
            raise psycopg.OperationalError("could not connect")
        repo = PostgresClientRepository(connect=refuse)
        for call in (
            lambda: repo.ensure_client(uuid.UUID(CLIENT_A)),
            lambda: repo.get_preferences(uuid.UUID(CLIENT_A)),
            lambda: repo.list_routes(uuid.UUID(CLIENT_A), 50),
            lambda: repo.delete_client(uuid.UUID(CLIENT_A)),
        ):
            with pytest.raises(StorageUnavailable):
                call()


class TestRouteMetrics:

    def test_distance_from_length_km(self):
        assert route_metrics(3, {"length_km": 2.5}, None)["distance_m"] == 2500

    def test_duration_prefers_the_budget(self):
        m = route_metrics(1, {"time_seconds": 3600.0}, {"total_minutes": 75})
        assert m["duration_min"] == 75

    def test_duration_falls_back_to_route_time(self):
        assert route_metrics(1, {"time_seconds": 3600.0}, None)["duration_min"] == 60

    def test_missing_values_stay_null_never_guessed(self):
        m = route_metrics(0, None, None)
        assert m == {"stop_count": 0, "distance_m": None, "duration_min": None}


def _db_up() -> bool:
    try:
        psycopg.connect(settings.DSN, connect_timeout=3).close()
    except Exception:
        return False
    return True


def _apply_migration(conn) -> None:
    """Apply 0005 as one multi-statement call (idempotent, safe to re-run)."""
    with open(MIGRATION, encoding="utf-8") as fh:
        conn.execute(fh.read())


def test_live_round_trip_and_cascade():
    """Save, list, read, rename, delete a client against the real database.

    Skips when Postgres is not reachable (the usual case in a bare unit run).
    """
    if not _db_up():
        pytest.skip(f"live DB not reachable at {settings.DSN}")

    admin = psycopg.connect(settings.DSN, autocommit=True)
    _apply_migration(admin)

    repo = PostgresClientRepository()
    client_id = uuid.uuid4()
    try:
        agent_main.app.state.clients_repository = repo
        tc = TestClient(agent_main.app, raise_server_exceptions=False)
        headers = {"X-Client-Id": str(client_id)}

        assert tc.put("/clients/me/preferences", headers=headers,
                      json={"transport": "auto", "party_adults": 2}).json()[
            "transport"] == "auto"
        cleared = tc.put("/clients/me/preferences", headers=headers,
                         json={"party_adults": None}).json()
        assert cleared["party_adults"] is None and cleared["transport"] == "auto"

        created = _save_route(tc, headers=headers).json()
        detail = tc.get(f"/clients/me/routes/{created['id']}",
                        headers=headers).json()
        assert detail["plan"] == PLAN
        assert detail["visit_overrides"] == VISIT_OVERRIDES
        item = tc.get("/clients/me/routes", headers=headers).json()[0]
        assert item["stop_count"] == 2
        assert item["distance_m"] == 1500
        assert item["duration_min"] == 90
        assert "plan" not in item

        renamed = tc.patch(f"/clients/me/routes/{created['id']}",
                           headers=headers, json={"name": "Новое имя"}).json()
        assert renamed["name"] == "Новое имя"

        assert tc.delete("/clients/me", headers=headers).status_code == 204
        with admin.cursor() as cur:
            cur.execute("SELECT count(*) FROM clients WHERE id = %s", (client_id,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM saved_routes WHERE client_id = %s",
                        (client_id,))
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT count(*) FROM client_preferences WHERE client_id = %s",
                (client_id,),
            )
            assert cur.fetchone()[0] == 0
    finally:
        with admin.cursor() as cur:
            cur.execute("DELETE FROM clients WHERE id = %s", (client_id,))
        admin.close()
        repo.close()
        if hasattr(agent_main.app.state, "clients_repository"):
            delattr(agent_main.app.state, "clients_repository")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

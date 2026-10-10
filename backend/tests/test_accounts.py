"""Accounts, roles, visits and the admin surface — server side.

No API key, DB or network: tests inject a fake repository into ``app.state``.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests._schema import baseline_sql

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api import main as agent_main
from core.passwords import hash_password, hash_token, verify_password
from db.models.user import AdminUser, User, VisitedPlace
from db.store.errors import DuplicateSource, EmailTaken, StorageUnavailable
from db.store.mappers import place_from_row

CLIENT = "33333333-3333-4333-8333-333333333333"

ADMIN_EMAIL = "boss@example.com"

GOOD_PW = "correct-horse-42"


def _now() -> datetime:
    return datetime.now(UTC)


def _place_row(
    pid: int,
    name: str,
    *,
    category: str | None = "замок",
    town: str | None = "Гродно",
    source_url: str | None = None,
    lat: float = 53.67,
    lon: float = 23.82,
    visit_minutes: int | None = 60,
) -> dict:
    """A row with every column ``place_payload`` reads, so payloads match live."""
    return {
        "id": pid,
        "source_url": source_url or f"test:place-{pid}",
        "name": name,
        "category": category,
        "town": town,
        "district": "Гродненский",
        "lat": lat,
        "lon": lon,
        "visit_minutes": visit_minutes,
        "opening_hours": None,
        "blurb": None,
        "fun_fact": None,
        "fun_facts": None,
        "links": None,
        "ticket_price": None,
        "photo_url": None,
        "photo_author": None,
        "photo_license": None,
        "photo_source": None,
    }


class FakeRepo:
    """In-memory account and place repository with the Postgres contract."""

    def __init__(self) -> None:
        self.users: dict[uuid.UUID, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.visited: dict[uuid.UUID, dict[int, datetime]] = {}
        self.saved_routes_count: dict[uuid.UUID, int] = {}
        self.places: dict[int, dict] = {
            1: _place_row(1, "Старый замок"),
            2: _place_row(2, "Новый замок"),
            3: _place_row(3, "Фарный костёл", category="костёл"),
        }

    @staticmethod
    def _public(row: dict) -> dict:
        return {k: v for k, v in row.items() if k != "password_hash"}

    def _by_email(self, email: str) -> dict | None:
        low = email.lower()
        for row in self.users.values():
            if row["email"].lower() == low:
                return row
        return None

    def create_user(self, user_id, *, email, password_hash, display_name=None,
                    role="user", client_id=None):
        if self._by_email(email) is not None:
            raise EmailTaken(email)
        if client_id is not None and any(
            r["client_id"] == client_id for r in self.users.values()
        ):
            client_id = None
        now = _now()
        row = {
            "id": user_id, "email": email, "password_hash": password_hash,
            "display_name": display_name, "role": role, "client_id": client_id,
            "created_at": now, "updated_at": now, "last_login_at": None,
        }
        self.users[user_id] = row
        return User(**self._public(row))

    def get_user_by_email(self, email):
        row = self._by_email(email)
        return User(**row) if row is not None else None

    def get_user(self, user_id):
        row = self.users.get(user_id)
        return User(**self._public(row)) if row is not None else None

    def link_client(self, user_id, client_id):
        row = self.users.get(user_id)
        if row is None or row["client_id"] is not None:
            return False
        if any(r["client_id"] == client_id for r in self.users.values()):
            return False
        row["client_id"] = client_id
        return True

    def touch_login(self, user_id):
        if user_id in self.users:
            self.users[user_id]["last_login_at"] = _now()

    def create_session(self, token_hash, user_id, expires_at):
        self.sessions[token_hash] = {"user_id": user_id, "expires_at": expires_at}

    def get_session_user(self, token_hash):
        sess = self.sessions.get(token_hash)
        if sess is None or sess["expires_at"] <= _now():
            return None
        return self.get_user(sess["user_id"])

    def delete_session(self, token_hash):
        self.sessions.pop(token_hash, None)

    def list_users(self, *, q="", limit=50, offset=0):
        rows = [self._public(r) for r in self.users.values()]
        if q:
            needle = q.lower()
            rows = [r for r in rows
                    if needle in r["email"].lower()
                    or needle in (r["display_name"] or "").lower()]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        total = len(rows)
        page = rows[offset:offset + limit]
        out = [
            AdminUser(
                **r,
                saved_routes=self.saved_routes_count.get(r["client_id"], 0)
                if r["client_id"] else 0,
                visited=len(self.visited.get(r["id"], {})),
            )
            for r in page
        ]
        return out, total

    def count_admins(self):
        return sum(1 for r in self.users.values() if r["role"] == "admin")

    def update_user(self, user_id, *, role=None, display_name=None,
                    display_name_set=False):
        row = self.users.get(user_id)
        if row is None:
            return None
        if role is not None:
            row["role"] = role
        if display_name_set:
            row["display_name"] = display_name
        row["updated_at"] = _now()
        return User(**self._public(row))

    def delete_user(self, user_id):
        if user_id not in self.users:
            return False
        del self.users[user_id]
        self.visited.pop(user_id, None)
        self.sessions = {k: v for k, v in self.sessions.items()
                         if v["user_id"] != user_id}
        return True

    def list_visited(self, user_id):
        marks = self.visited.get(user_id, {})
        return [
            VisitedPlace(user_id=user_id, place_id=pid, visited_at=at)
            for pid, at in sorted(marks.items(),
                                  key=lambda kv: kv[1], reverse=True)
        ]

    def mark_visited(self, user_id, place_id):
        if place_id not in self.places:
            return None
        marks = self.visited.setdefault(user_id, {})
        at = marks.get(place_id, _now())
        marks[place_id] = at
        return VisitedPlace(user_id=user_id, place_id=place_id, visited_at=at)

    def mark_visited_many(self, user_id, place_ids):
        marks = self.visited.setdefault(user_id, {})
        marked = []
        for pid in place_ids:
            if pid in self.places and pid not in marks:
                marks[pid] = _now()
                marked.append(pid)
        return marked

    def unmark_visited(self, user_id, place_id):
        return self.visited.get(user_id, {}).pop(place_id, None) is not None

    # --- places: the admin CRUD and the visited payloads --------------------

    def get_by_id(self, place_id):
        row = self.places.get(place_id)
        return place_from_row(row) if row is not None else None

    def get_by_ids(self, ids):
        return [place_from_row(self.places[pid]) for pid in ids if pid in self.places]

    def list_places_paged(self, *, q="", category="", limit=50, offset=0):
        rows = list(self.places.values())
        if q:
            needle = q.lower()
            rows = [r for r in rows if needle in r["name"].lower()
                    or needle in (r["town"] or "").lower()]
        if category:
            rows = [r for r in rows if r["category"] == category]
        rows.sort(key=lambda r: r["name"])
        total = len(rows)
        page = rows[offset:offset + limit]
        return [place_from_row(r) for r in page], total

    def create_place(self, fields):
        if any(p["source_url"] == fields.get("source_url")
               for p in self.places.values()):
            raise DuplicateSource(str(fields.get("source_url")))
        pid = max(self.places, default=0) + 1
        row = _place_row(pid, fields["name"])
        row.update({k: v for k, v in fields.items() if k in row})
        self.places[pid] = row
        return place_from_row(row)

    def update_place(self, place_id, fields):
        row = self.places.get(place_id)
        if row is None:
            return None
        if "source_url" in fields and any(
            p["id"] != place_id and p["source_url"] == fields["source_url"]
            for p in self.places.values()
        ):
            raise DuplicateSource(str(fields["source_url"]))
        row.update({k: v for k, v in fields.items() if k in row})
        return place_from_row(row)

    def delete_place(self, place_id):
        return self.places.pop(place_id, None) is not None

    def stats(self):
        return {
            "users": len(self.users),
            "admins": self.count_admins(),
            "places": len(self.places),
            "visited": sum(len(v) for v in self.visited.values()),
            "saved_routes": sum(self.saved_routes_count.values()),
        }


class DownRepo:
    """Every method fails the way an unreachable database does."""

    def __getattr__(self, _name):
        def _boom(*_a, **_kw):
            raise StorageUnavailable("connection refused")
        return _boom


_REPO_ATTRS = ("users_repository", "stats_repository", "places_repository")


def _install(repo) -> None:
    """Point every accessor the account and admin routers use at one repository."""
    for attr in _REPO_ATTRS:
        setattr(agent_main.app.state, attr, repo)


def _uninstall() -> None:
    for attr in _REPO_ATTRS:
        if hasattr(agent_main.app.state, attr):
            delattr(agent_main.app.state, attr)


@pytest.fixture
def repo():
    fake = FakeRepo()
    _install(fake)
    yield fake
    _uninstall()


@pytest.fixture
def client(repo):
    return TestClient(agent_main.app, raise_server_exceptions=False)


def _register(client, email="tourist@example.com", password=GOOD_PW,
              display_name=None, headers=None):
    body = {"email": email, "password": password}
    if display_name is not None:
        body["display_name"] = display_name
    return client.post("/auth/register", json=body, headers=headers)


def _login(client, email="tourist@example.com", password=GOOD_PW):
    return client.post("/auth/login", json={"email": email, "password": password})


def _make_admin(repo, email=ADMIN_EMAIL, password=GOOD_PW):
    uid = uuid.uuid4()
    repo.create_user(uid, email=email, password_hash=hash_password(password),
                     display_name="Boss", role="admin")
    return uid


def _admin_client(repo, email=ADMIN_EMAIL, password=GOOD_PW):
    _make_admin(repo, email=email, password=password)
    tc = TestClient(agent_main.app, raise_server_exceptions=False)
    assert _login(tc, email, password).status_code == 200
    return tc


class TestRegister:

    def test_creates_a_user_and_signs_it_in(self, client, repo):
        r = _register(client, display_name="Максим")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["email"] == "tourist@example.com"
        assert body["role"] == "user"
        assert body["display_name"] == "Максим"
        assert "grodno_session" in client.cookies
        me = client.get("/auth/me").json()
        assert me["authenticated"] is True
        assert me["user"]["email"] == "tourist@example.com"

    def test_never_returns_the_password_hash(self, client):
        body = _register(client).json()
        assert "password_hash" not in body
        assert "password" not in body
        assert "password_hash" not in client.get("/auth/me").json()["user"]

    def test_stores_a_hash_not_the_plaintext(self, client, repo):
        _register(client)
        row = repo._by_email("tourist@example.com")
        assert row["password_hash"] != GOOD_PW
        assert verify_password(GOOD_PW, row["password_hash"])

    def test_email_is_case_insensitive(self, client):
        assert _register(client, email="Tourist@Example.COM").status_code == 201
        assert _register(client, email="tourist@example.com").status_code == 409

    def test_duplicate_email_is_email_taken(self, client):
        _register(client)
        r = _register(client)
        assert r.status_code == 409
        assert r.json() == {"reason": "email_taken"}

    def test_invalid_email_is_a_machine_code(self, client):
        r = _register(client, email="not-an-email")
        assert r.status_code == 422
        assert r.json() == {"reason": "invalid_email"}

    def test_weak_password_is_a_machine_code(self, client):
        r = _register(client, password="short")
        assert r.status_code == 422
        assert r.json() == {"reason": "weak_password"}

    def test_adopts_the_anonymous_client(self, client, repo):
        r = _register(client, headers={"X-Client-Id": CLIENT})
        assert r.status_code == 201
        owner = next(iter(repo.users.values()))
        assert str(owner["client_id"]) == CLIENT

    def test_a_malformed_client_header_is_ignored(self, client, repo):
        r = _register(client, headers={"X-Client-Id": "not-a-uuid"})
        assert r.status_code == 201
        assert next(iter(repo.users.values()))["client_id"] is None


class TestSession:

    def test_login_with_correct_password(self, client):
        _register(client)
        client.post("/auth/logout")
        r = _login(client)
        assert r.status_code == 200, r.text
        assert client.get("/auth/me").json()["authenticated"] is True

    def test_wrong_password_and_unknown_email_are_the_same_code(self, client):
        _register(client)
        client.post("/auth/logout")
        bad_pw = _login(client, password="wrong-password")
        unknown = _login(client, email="nobody@example.com")
        assert bad_pw.status_code == 401
        assert unknown.status_code == 401
        assert bad_pw.json() == unknown.json() == {"reason": "invalid_credentials"}

    def test_logout_clears_the_session(self, client, repo):
        _register(client)
        assert client.post("/auth/logout").status_code == 204
        assert client.get("/auth/me").json() == {"authenticated": False, "user": None}
        assert repo.sessions == {}

    def test_me_without_a_cookie_is_the_anonymous_state(self, client):
        r = client.get("/auth/me")
        assert r.status_code == 200
        assert r.json() == {"authenticated": False, "user": None}

    def test_an_expired_session_is_not_a_session(self, client, repo):
        _register(client)
        for sess in repo.sessions.values():
            sess["expires_at"] = _now() - timedelta(seconds=1)
        assert client.get("/auth/me").json()["authenticated"] is False


class TestVisits:

    def test_unauthenticated_write_is_401(self, client):
        assert client.put("/me/visited/1").status_code == 401
        assert client.get("/me/visited").status_code == 401

    def test_mark_then_list_then_unmark(self, client):
        _register(client)
        r = client.put("/me/visited/1")
        assert r.status_code == 200, r.text
        assert r.json()["place_id"] == 1
        assert r.json()["name"] == "Старый замок"
        assert r.json()["visited_at"]

        listed = client.get("/me/visited").json()
        assert listed["count"] == 1
        assert listed["items"][0]["place_id"] == 1

        assert client.delete("/me/visited/1").status_code == 204
        assert client.get("/me/visited").json() == {"items": [], "count": 0}

    def test_marking_is_idempotent(self, client):
        _register(client)
        first = client.put("/me/visited/1").json()["visited_at"]
        second = client.put("/me/visited/1").json()["visited_at"]
        assert first == second
        assert client.get("/me/visited").json()["count"] == 1

    def test_unmark_is_idempotent(self, client):
        _register(client)
        assert client.delete("/me/visited/2").status_code == 204
        assert client.delete("/me/visited/2").status_code == 204

    def test_unknown_place_is_place_not_found(self, client):
        _register(client)
        r = client.put("/me/visited/9999")
        assert r.status_code == 404
        assert r.json() == {"reason": "place_not_found"}

    def test_bulk_mark_skips_unknown_ids(self, client):
        _register(client)
        r = client.post("/me/visited", json={"place_ids": [1, 2, 9999]})
        assert r.status_code == 200, r.text
        assert sorted(r.json()["marked"]) == [1, 2]
        assert r.json()["count"] == 2

    def test_visits_are_per_user(self, client, repo):
        _register(client, email="a@example.com")
        client.put("/me/visited/1")
        client.post("/auth/logout")
        _register(client, email="b@example.com")
        assert client.get("/me/visited").json()["count"] == 0


class TestAdminAccess:

    def test_anonymous_is_401(self, client):
        assert client.get("/admin/users").status_code == 401
        assert client.get("/admin/stats").status_code == 401

    def test_a_plain_user_is_403_not_admin(self, client):
        _register(client)
        for path in ("/admin/users", "/admin/places", "/admin/stats"):
            r = client.get(path)
            assert r.status_code == 403, path
            assert r.json() == {"reason": "not_admin"}

    def test_an_admin_passes(self, repo):
        admin = _admin_client(repo)
        assert admin.get("/admin/stats").status_code == 200


class TestAdminUsers:

    def test_list_carries_the_counts(self, repo):
        admin = _admin_client(repo)
        _register(admin, email="tourist@example.com")
        admin.put("/me/visited/1")
        admin.post("/auth/logout")
        assert _login(admin, ADMIN_EMAIL).status_code == 200

        body = admin.get("/admin/users").json()
        assert body["total"] == 2
        tourist = next(u for u in body["items"] if u["email"] == "tourist@example.com")
        assert tourist["visited"] == 1
        assert tourist["role"] == "user"

    def test_promote_and_demote(self, repo):
        admin = _admin_client(repo)
        _register(admin, email="tourist@example.com")
        admin.post("/auth/logout")
        assert _login(admin, ADMIN_EMAIL).status_code == 200
        tourist_id = next(k for k, v in repo.users.items()
                          if v["email"] == "tourist@example.com")

        promoted = admin.patch(f"/admin/users/{tourist_id}", json={"role": "admin"})
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["role"] == "admin"

        demoted = admin.patch(f"/admin/users/{tourist_id}", json={"role": "user"})
        assert demoted.json()["role"] == "user"

    def test_cannot_demote_the_last_admin(self, repo):
        admin = _admin_client(repo)
        admin_id = next(iter(repo.users))
        r = admin.patch(f"/admin/users/{admin_id}", json={"role": "user"})
        assert r.status_code == 409
        assert r.json()["reason"] in {"self_role", "last_admin"}

    def test_cannot_delete_self(self, repo):
        admin = _admin_client(repo)
        admin_id = next(iter(repo.users))
        r = admin.delete(f"/admin/users/{admin_id}")
        assert r.status_code == 409
        assert r.json() == {"reason": "self_delete"}

    def test_can_delete_another_user(self, repo):
        admin = _admin_client(repo)
        _register(admin, email="tourist@example.com")
        admin.post("/auth/logout")
        assert _login(admin, ADMIN_EMAIL).status_code == 200
        uid = next(k for k, v in repo.users.items()
                   if v["email"] == "tourist@example.com")
        assert admin.delete(f"/admin/users/{uid}").status_code == 204
        assert admin.get("/admin/users").json()["total"] == 1

    def test_unknown_user_is_404(self, repo):
        admin = _admin_client(repo)
        assert admin.patch(f"/admin/users/{uuid.uuid4()}",
                           json={"role": "admin"}).status_code == 404
        assert admin.delete(f"/admin/users/{uuid.uuid4()}").status_code == 404


class TestAdminPlaces:

    def test_list_and_search(self, repo):
        admin = _admin_client(repo)
        body = admin.get("/admin/places").json()
        assert body["total"] == 3
        one = admin.get("/admin/places?q=замок").json()
        assert {i["name"] for i in one["items"]} == {"Старый замок", "Новый замок"}
        cott = admin.get("/admin/places?category=костёл").json()
        assert [i["name"] for i in cott["items"]] == ["Фарный костёл"]

    def test_create_edit_delete(self, repo):
        admin = _admin_client(repo)
        created = admin.post("/admin/places", json={
            "name": "Кафе у замка", "lat": 53.68, "lon": 23.83,
            "source_url": "test:new-cafe", "category": "кафе", "town": "Гродно",
        })
        assert created.status_code == 201, created.text
        pid = created.json()["place_id"]
        assert created.json()["name"] == "Кафе у замка"

        edited = admin.patch(f"/admin/places/{pid}",
                             json={"name": "Кафе «У замка»", "visit_minutes": 30})
        assert edited.status_code == 200
        assert edited.json()["name"] == "Кафе «У замка»"
        assert edited.json()["visit_minutes"] == 30

        assert admin.delete(f"/admin/places/{pid}").status_code == 204
        assert admin.patch(f"/admin/places/{pid}", json={"name": "x"}).status_code == 404

    def test_duplicate_source_is_409(self, repo):
        admin = _admin_client(repo)
        r = admin.post("/admin/places", json={
            "name": "Дубль", "lat": 1.0, "lon": 2.0,
            "source_url": "test:place-1",
        })
        assert r.status_code == 409
        assert r.json() == {"reason": "source_taken"}

    def test_empty_patch_is_rejected(self, repo):
        admin = _admin_client(repo)
        r = admin.patch("/admin/places/1", json={})
        assert r.status_code == 422
        assert r.json() == {"reason": "invalid_request"}


class TestStorageDown:

    @pytest.fixture
    def down(self):
        _install(DownRepo())
        yield TestClient(agent_main.app, raise_server_exceptions=False)
        _uninstall()

    @pytest.mark.parametrize("method,path,body", [
        ("post", "/auth/register", {"email": "a@b.co", "password": GOOD_PW}),
        ("post", "/auth/login", {"email": "a@b.co", "password": GOOD_PW}),
        ("get", "/auth/me", None),
        ("post", "/auth/logout", None),
        ("get", "/me/visited", None),
        ("put", "/me/visited/1", None),
        ("get", "/admin/users", None),
        ("get", "/admin/places", None),
        ("get", "/admin/stats", None),
        ("post", "/admin/places", {
            "name": "x", "lat": 1.0, "lon": 2.0, "source_url": "s",
        }),
    ])
    def test_every_endpoint_degrades_to_503(self, down, method, path, body):
        headers = {"Authorization": "Bearer any-token-shape"}
        r = getattr(down, method)(path, headers=headers, **json_body(body))
        assert r.status_code == 503, r.text
        assert r.json() == {"reason": "storage_unavailable"}


def json_body(body):
    return {"json": body} if body is not None else {}


class TestPasswords:

    def test_hash_is_salted_and_verifies(self):
        a = hash_password(GOOD_PW)
        b = hash_password(GOOD_PW)
        assert a != b
        assert verify_password(GOOD_PW, a)
        assert not verify_password("wrong", a)

    def test_malformed_or_missing_hash_reads_as_wrong(self):
        assert not verify_password(GOOD_PW, None)
        assert not verify_password(GOOD_PW, "")
        assert not verify_password(GOOD_PW, "not-a-hash")
        assert not verify_password(GOOD_PW, "argon2$1$2$3$yQ$yQ")

    def test_token_hash_is_a_digest(self):
        token = "some-token"
        assert hash_token(token) == hash_token(token)
        assert token not in hash_token(token)


def test_place_patch_forbids_null_coordinates():
    """Omitting a coordinate means «leave it»; an explicit null is a client bug.

    `lat`/`lon` are NOT NULL, so a null would be a 500; the model turns it into a 422.
    """
    from pydantic import ValidationError

    from api.models.accounts import AdminPlacePatch

    omitted = AdminPlacePatch(name="Старый замок")
    assert omitted.lat is None
    assert "lat" not in omitted.model_fields_set

    moved = AdminPlacePatch(lat=53.7, lon=23.9)
    assert (moved.lat, moved.lon) == (53.7, 23.9)

    for bad in ({"lat": None}, {"lon": None}):
        with pytest.raises(ValidationError):
            AdminPlacePatch(**bad)


BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _db_up() -> bool:
    import psycopg

    from core.config import settings

    try:
        psycopg.connect(settings.DSN, connect_timeout=3).close()
    except Exception:
        return False
    return True


def _apply_migration(conn) -> None:
    """Apply the schema baseline in one multi-statement call (idempotent)."""
    conn.execute(baseline_sql())


def test_live_account_visit_and_place_edit():
    """Register, a durable visit and an admin edit against the real database.

    The half the fake cannot prove: the citext index, the visit upsert, the category guard.
    """
    if not _db_up():
        pytest.skip("live DB not reachable")

    import psycopg

    from core.config import settings
    from db.store.places import PostgresPlaceRepository
    from db.store.stats import PostgresStatsRepository
    from db.store.users import PostgresUserRepository

    admin = psycopg.connect(settings.DSN, autocommit=True)
    _apply_migration(admin)

    users = PostgresUserRepository()
    places = PostgresPlaceRepository()
    stats = PostgresStatsRepository()
    agent_main.app.state.users_repository = users
    agent_main.app.state.places_repository = places
    agent_main.app.state.stats_repository = stats
    tc = TestClient(agent_main.app, raise_server_exceptions=False)
    email = f"live-{uuid.uuid4().hex[:10]}@example.com"
    place_id: int | None = None
    old_category = old_source = old_blurb = None
    try:
        with admin.cursor() as cur:
            cur.execute(
                "SELECT id, category, category_source, blurb FROM places "
                "WHERE category_source IN ('curated','dataset') "
                "AND category IS NOT NULL ORDER BY id LIMIT 1"
            )
            row = cur.fetchone()
        if row is None:
            pytest.skip("no guarded place in the dataset")
        place_id, old_category, old_source, old_blurb = row

        registered = tc.post(
            "/auth/register", json={"email": email, "password": GOOD_PW}
        )
        assert registered.status_code == 201, registered.text
        uid = registered.json()["id"]
        assert tc.get("/auth/me").json()["user"]["email"] == email

        assert tc.put(f"/me/visited/{place_id}").status_code == 200
        assert tc.put(f"/me/visited/{place_id}").status_code == 200
        assert tc.get("/me/visited").json()["count"] == 1

        assert tc.get("/admin/stats").status_code == 403
        with admin.cursor() as cur:
            cur.execute("UPDATE users SET role='admin' WHERE id=%s", (uid,))
        assert tc.get("/admin/stats").status_code == 200

        edited = tc.patch(
            f"/admin/places/{place_id}",
            json={"category": "live-test-category", "blurb": "live-test"},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["category"] == "live-test-category"

        with admin.cursor() as cur:
            cur.execute(
                "SELECT category, category_source FROM places WHERE id=%s",
                (place_id,),
            )
            got = cur.fetchone()
        assert got is not None
        assert got[0] == "live-test-category"
        assert got[1] == "curated"
    finally:
        cleanup = psycopg.connect(settings.DSN)
        try:
            with cleanup.cursor() as cur:
                cur.execute(
                    "SELECT set_config"
                    "('grodno.allow_curated_category_change','on',true)"
                )
                if place_id is not None:
                    cur.execute(
                        "UPDATE places SET category=%s, category_source=%s, "
                        "blurb=%s WHERE id=%s",
                        (old_category, old_source, old_blurb, place_id),
                    )
                cur.execute("DELETE FROM users WHERE email=%s", (email,))
            cleanup.commit()
        finally:
            cleanup.close()
        admin.close()
        users.close()
        places.close()
        stats.close()
        _uninstall()

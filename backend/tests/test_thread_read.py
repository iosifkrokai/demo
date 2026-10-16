"""Reading a thread back: the endpoint, its account-scoping, and the wire shape."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import thread
from api.models.accounts import SESSION_COOKIE
from api.routers import routes as routes_api
from core.passwords import hash_token
from db.models.thread import Thread, Turn
from db.models.user import User

TOKEN_A = "token-for-account-a"
TOKEN_B = "token-for-account-b"
SESSION = "run-read-1"


class FakeThreadRepository:
    """An in-memory journal, keyed exactly as the store keys it."""

    def __init__(self) -> None:
        self._turns: dict[str, list[Turn]] = {}
        self._appended: list[Turn] = []

    def append(
        self,
        thread_id: str,
        *,
        query: str,
        submission: dict[str, Any],
        measurement: dict[str, Any],
    ) -> Turn:
        rows = self._turns.setdefault(thread_id, [])
        turn = Turn(
            thread_id=thread_id,
            revision=len(rows),
            query=query,
            submission=submission,
            measurement=measurement,
        )
        rows.append(turn)
        self._appended.append(turn)
        return turn

    def latest(self, thread_id: str) -> Turn | None:
        rows = self._turns.get(thread_id) or []
        return rows[-1] if rows else None

    def latest_with_prefix(self, prefix: str) -> Turn | None:
        for turn in reversed(self._appended):
            if turn.thread_id.startswith(prefix):
                return turn
        return None

    def journal(self, thread_id: str) -> Thread | None:
        rows = self._turns.get(thread_id)
        if rows is None:
            return None
        return Thread(id=thread_id, turns=tuple(rows))

    def delete(self, thread_id: str) -> bool:
        return self._turns.pop(thread_id, None) is not None


class FakeUsers:
    """Only the one lookup the session gate needs."""

    def __init__(self, *token_hashes: str) -> None:
        self._users = {h: _user() for h in token_hashes}

    def get_session_user(self, token_hash: str) -> User | None:
        return self._users.get(token_hash)


def _user() -> User:
    return User(id=uuid.uuid4(), email="tourist@example.com", role="user")


def _app() -> tuple[TestClient, FakeThreadRepository]:
    repo = FakeThreadRepository()
    app = FastAPI()
    app.include_router(routes_api.router)
    app.state.repos = SimpleNamespace(threads=repo)
    app.state.users_repository = FakeUsers(hash_token(TOKEN_A), hash_token(TOKEN_B))
    return TestClient(app), repo


def _as(client: TestClient, token: str) -> None:
    client.cookies.set(SESSION_COOKIE, token)


def _key(token: str, session: str = SESSION) -> str:
    return thread.thread_key(session, identity=hash_token(token))


def test_the_key_carries_the_session_that_names_the_run() -> None:
    assert thread.thread_key("run-1", identity="abc") == "abc:run-1"


def test_an_anonymous_key_is_the_bare_run() -> None:
    assert thread.thread_key("run-1") == "run-1"
    assert thread.thread_key("run-1", identity=None) == "run-1"


def test_an_empty_thread_reads_back_empty() -> None:
    client, _ = _app()
    _as(client, TOKEN_A)

    response = client.get(f"/routes/threads/{SESSION}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == SESSION
    assert body["turns"] == []


def test_one_recorded_turn_comes_back_whole() -> None:
    client, repo = _app()
    repo.append(
        _key(TOKEN_A),
        query="погулять по старому городу",
        submission={"ordered_ids": [1, 2]},
        measurement={"status": "ready", "results": [{"id": 1, "name": "Старый замок"}]},
    )
    _as(client, TOKEN_A)

    response = client.get(f"/routes/threads/{SESSION}")

    assert response.status_code == 200, response.text
    turns = response.json()["turns"]
    assert len(turns) == 1
    assert turns[0]["revision"] == 0
    assert turns[0]["query"] == "погулять по старому городу"
    assert turns[0]["submission"] == {"ordered_ids": [1, 2]}
    assert turns[0]["measurement"]["status"] == "ready"
    assert "created_at" in turns[0]


def test_several_turns_come_back_in_revision_order() -> None:
    client, repo = _app()
    for index in range(3):
        repo.append(
            _key(TOKEN_A),
            query=f"уточнение {index}",
            submission={"ordered_ids": [index]},
            measurement={"status": "ready"},
        )
    _as(client, TOKEN_A)

    response = client.get(f"/routes/threads/{SESSION}")

    assert response.status_code == 200, response.text
    turns = response.json()["turns"]
    assert [turn["revision"] for turn in turns] == [0, 1, 2]
    assert [turn["query"] for turn in turns] == ["уточнение 0", "уточнение 1", "уточнение 2"]


def test_another_accounts_thread_is_not_readable() -> None:
    """The same run id under a different session reads an empty thread, not the one written."""
    client, repo = _app()
    repo.append(
        _key(TOKEN_A),
        query="чужой маршрут",
        submission={"ordered_ids": [1]},
        measurement={"status": "ready"},
    )

    _as(client, TOKEN_A)
    assert len(client.get(f"/routes/threads/{SESSION}").json()["turns"]) == 1

    _as(client, TOKEN_B)
    other = client.get(f"/routes/threads/{SESSION}")

    assert other.status_code == 200, other.text
    assert other.json()["turns"] == []


def test_reading_without_a_session_is_refused() -> None:
    client, _ = _app()

    response = client.get(f"/routes/threads/{SESSION}")

    assert response.status_code == 401
    assert response.json() == {"reason": "not_authenticated"}

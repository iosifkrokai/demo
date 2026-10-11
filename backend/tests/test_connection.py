"""Every connection leaves `db.connection.connect` ready to use.

The trigram threshold is a session GUC — Postgres does not persist it, so a
connection that is handed out without it silently searches at the 0.3 default.
These tests pin that setup, and that it does not leave a transaction dangling.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg import sql

from db import connection


class _FakeConn:
    """Records what `connect` does to a connection before handing it back."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.commits = 0
        self.closed = False
        self.kwargs: dict = {}

    def execute(self, query: object) -> None:
        if isinstance(query, sql.Composable):
            self.executed.append(query.as_string(None))
        else:
            self.executed.append(str(query))

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake(monkeypatch) -> _FakeConn:
    conn = _FakeConn()

    def _connect(dsn, **kwargs):
        conn.kwargs = {"dsn": dsn, **kwargs}
        return conn

    monkeypatch.setattr(psycopg, "connect", _connect)
    return conn


def test_connect_sets_the_trigram_threshold(fake: _FakeConn) -> None:
    connection.connect()

    assert fake.executed == [
        f"SET pg_trgm.word_similarity_threshold = {connection.WORD_SIMILARITY_THRESHOLD}"
    ]


def test_autocommit_connection_is_not_committed(fake: _FakeConn) -> None:
    """There is no transaction to commit, and committing would be a no-op anyway."""
    connection.connect(autocommit=True)

    assert fake.commits == 0


def test_transactional_connection_starts_clean(fake: _FakeConn) -> None:
    """The setup statement is committed, so the caller owns a fresh transaction."""
    connection.connect()

    assert fake.commits == 1


def test_libpq_gets_an_int_timeout(fake: _FakeConn) -> None:
    """`connect_timeout` is an int in libpq; a float raises at connect time."""
    connection.connect(timeout=3.0)

    assert fake.kwargs["connect_timeout"] == 3


def test_no_timeout_means_no_connect_timeout(fake: _FakeConn) -> None:
    connection.connect(timeout=None)

    assert "connect_timeout" not in fake.kwargs


def test_a_failed_setup_closes_the_connection(monkeypatch) -> None:
    """A half-configured connection must not escape into the pool of callers."""
    conn = _FakeConn()

    def _boom(sql: str) -> None:
        raise psycopg.OperationalError("no pg_trgm")

    conn.execute = _boom  # type: ignore[method-assign]
    monkeypatch.setattr(psycopg, "connect", lambda dsn, **kwargs: conn)

    with pytest.raises(psycopg.OperationalError):
        connection.connect()

    assert conn.closed

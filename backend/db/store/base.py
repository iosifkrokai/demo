"""What every Postgres repository shares: one lazy connection, two cursors.

A repository is handed a connection *factory*, not a connection. Reconnection,
thread-safety and the mapping from `psycopg.Error` to `StorageUnavailable` live
here, so no caller has to think about them — which is what passing a bare
connection around used to push onto every layer above.

`_tx_cursor` is not decoration: the connection is autocommit, so a `SET LOCAL`
or a trigger that reads transaction state needs a real transaction around it.
That is exactly what keeps the curated-category guard honest.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from db.connection import connect
from db.store.errors import StorageUnavailable

ConnectionFactory = Callable[[], psycopg.Connection]


def autocommit_connect() -> psycopg.Connection:
    """The default factory: one autocommit connection through the configured DSN.

    The connect timeout inside `db.connection` is short, so a database that is
    down fails fast into a ``503`` rather than hanging the request.
    """
    return connect(autocommit=True)


class PostgresRepository:
    """A lazy, reused connection; a lock serialises access to it."""

    def __init__(self, connect: ConnectionFactory | None = None) -> None:
        self._connect = connect or autocommit_connect
        self._conn: psycopg.Connection | None = None
        self._lock = threading.Lock()

    def _connection(self) -> psycopg.Connection:
        conn = self._conn
        if conn is None or conn.closed:
            conn = self._connect()
            self._conn = conn
        return conn

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):  # pragma: no cover — a broken socket
                conn.close()

    @contextmanager
    def _cursor(self) -> Generator[Any]:
        """A dict-row cursor, or :class:`StorageUnavailable` on any DB failure."""
        try:
            with self._lock, self._connection().cursor(row_factory=dict_row) as cur:
                yield cur
        except psycopg.Error as exc:
            self._drop()
            raise StorageUnavailable(str(exc)) from exc

    @contextmanager
    def _tx_cursor(self) -> Generator[Any]:
        """A cursor inside an explicit transaction, for `SET LOCAL` and triggers."""
        try:
            with self._lock:
                conn = self._connection()
                with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
                    yield cur
        except psycopg.Error as exc:
            self._drop()
            raise StorageUnavailable(str(exc)) from exc

    def commit(self) -> None:
        """Flush the current transaction, if the connection has one.

        A no-op on the autocommit connections the app hands out; the seed runs
        non-autocommit and needs it after each batch.
        """
        conn = self._conn
        if conn is not None and not conn.closed and not conn.autocommit:
            try:
                conn.commit()
            except psycopg.Error as exc:
                self._drop()
                raise StorageUnavailable(str(exc)) from exc

    def close(self) -> None:
        with self._lock:
            self._drop()

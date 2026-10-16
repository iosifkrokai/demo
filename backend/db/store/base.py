"""What every Postgres repository shares: one lazy connection, two cursors."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from db.connection import connect
from db.store.errors import StorageUnavailableError

ConnectionFactory = Callable[[], psycopg.Connection]


def autocommit_connect() -> psycopg.Connection:
    """The default factory: one autocommit connection through the configured DSN."""
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
            with contextlib.suppress(Exception):
                conn.close()

    @contextmanager
    def _cursor(self) -> Generator[Any]:
        """A dict-row cursor, or :class:`StorageUnavailableError` on any DB failure."""
        try:
            with self._lock, self._connection().cursor(row_factory=dict_row) as cur:
                yield cur
        except psycopg.Error as exc:
            self._drop()
            raise StorageUnavailableError(str(exc)) from exc

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
            raise StorageUnavailableError(str(exc)) from exc

    def commit(self) -> None:
        """Flush the current transaction, if the connection has one."""
        conn = self._conn
        if conn is not None and not conn.closed and not conn.autocommit:
            try:
                conn.commit()
            except psycopg.Error as exc:
                self._drop()
                raise StorageUnavailableError(str(exc)) from exc

    def close(self) -> None:
        with self._lock:
            self._drop()

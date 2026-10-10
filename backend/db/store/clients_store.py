"""Storage for the anonymous client entity.

One job: persist a client's preferences and saved routes and answer nothing else.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from db.connection import connect
from db.store.mappers import route_metrics

log = logging.getLogger(__name__)

MAX_SAVED_ROUTES = 200

PREFERENCE_COLUMNS = (
    "transport",
    "time_budget_minutes",
    "party_adults",
    "party_children",
    "interests",
    "language",
    "visit_minutes_by_category",
)

_PREFERENCE_SELECT = (
    "transport, time_budget_minutes, party_adults, party_children, "
    "interests, language, visit_minutes_by_category, updated_at"
)

_ROUTE_DETAIL_SELECT = (
    "id, name, query, plan, visit_overrides, created_at, updated_at"
)


class StorageUnavailable(RuntimeError):
    """The client store could not reach Postgres (→ ``503 storage_unavailable``)."""


class TooManyRoutes(RuntimeError):
    """The client already holds ``limit`` saved routes."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"client already has {limit} saved routes")
        self.limit = limit


class ClientRepository(Protocol):
    """The surface clients_api depends on (a fake implements exactly this)."""

    def ensure_client(self, client_id: uuid.UUID) -> None: ...

    def get_preferences(self, client_id: uuid.UUID) -> dict[str, Any] | None: ...

    def upsert_preferences(
        self, client_id: uuid.UUID, fields: dict[str, Any]
    ) -> dict[str, Any]: ...

    def add_route(
        self,
        client_id: uuid.UUID,
        route_id: uuid.UUID,
        *,
        query: str,
        plan: dict[str, Any],
        name: str | None = None,
        visit_overrides: dict[str, int] | None = None,
    ) -> dict[str, Any]: ...

    def list_routes(
        self, client_id: uuid.UUID, limit: int = 50
    ) -> list[dict[str, Any]]: ...

    def get_route(
        self, client_id: uuid.UUID, route_id: uuid.UUID
    ) -> dict[str, Any] | None: ...

    def rename_route(
        self, client_id: uuid.UUID, route_id: uuid.UUID, name: str
    ) -> dict[str, Any] | None: ...

    def delete_route(self, client_id: uuid.UUID, route_id: uuid.UUID) -> bool: ...

    def delete_client(self, client_id: uuid.UUID) -> bool: ...


def default_connect() -> psycopg.Connection:
    """One autocommit connection through the DSN from config.py.

    ``connect_timeout`` is short so a down database fails fast into ``503``.
    """
    return connect(autocommit=True)


def _adapt(value: Any, column: str) -> Any:
    """Adapt a Python value for psycopg.

    ``None`` stays SQL NULL; a dict/list is wrapped as jsonb, not a string.
    """
    if value is None:
        return None
    if column in ("plan", "visit_overrides", "visit_minutes_by_category"):
        return Jsonb(value)
    return value


class PostgresClientRepository:
    """A :class:`ClientRepository` backed by Postgres.

    The connection is lazy and reused; a lock serialises concurrent access.
    """

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection] | None = None,
        *,
        max_routes: int = MAX_SAVED_ROUTES,
    ) -> None:
        self._connect = connect or default_connect
        self._conn: psycopg.Connection | None = None
        self._lock = threading.Lock()
        self.max_routes = max_routes

    def _connection(self) -> psycopg.Connection:
        conn = self._conn
        if conn is None or conn.closed:
            conn = self._connect()
            self._conn = conn
        return conn

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        """A dict-row cursor, or :class:`StorageUnavailable` on any DB failure."""
        try:
            with self._lock:
                conn = self._connection()
                with conn.cursor(row_factory=dict_row) as cur:
                    yield cur
        except psycopg.Error as exc:
            self._drop()
            raise StorageUnavailable(str(exc)) from exc

    def close(self) -> None:
        with self._lock:
            self._drop()

    def ensure_client(self, client_id: uuid.UUID) -> None:
        """Insert the client if unseen, otherwise refresh ``last_seen_at``."""
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO clients (id, created_at, last_seen_at)
                VALUES (%s, now(), now())
                ON CONFLICT (id) DO UPDATE SET last_seen_at = now()
                """,
                (client_id,),
            )

    def delete_client(self, client_id: uuid.UUID) -> bool:
        """Remove the client; preferences and routes cascade away with it."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM clients WHERE id = %s", (client_id,))
            return cur.rowcount > 0

    def get_preferences(self, client_id: uuid.UUID) -> dict[str, Any] | None:
        """The stored row, or ``None`` when nothing has been saved yet."""
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {_PREFERENCE_SELECT} FROM client_preferences "
                "WHERE client_id = %s",
                (client_id,),
            )
            row = cur.fetchone()
        return dict(row) if row is not None else None

    def upsert_preferences(
        self, client_id: uuid.UUID, fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply a partial update: only ``fields`` change, ``None`` clears.

        Creates the preferences row if this client has none yet.
        """
        self.ensure_client(client_id)
        cols = [c for c in fields if c in PREFERENCE_COLUMNS]
        params: list[Any] = [client_id]
        insert_cols = ["client_id", *cols]
        insert_vals = ["%s", *(["%s"] * len(cols))]
        params.extend(_adapt(fields[c], c) for c in cols)
        update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        if update_set:
            update_set += ", "
        sql = (
            f"INSERT INTO client_preferences ({', '.join(insert_cols)}, updated_at) "
            f"VALUES ({', '.join(insert_vals)}, now()) "
            f"ON CONFLICT (client_id) DO UPDATE SET {update_set}updated_at = now() "
            f"RETURNING {_PREFERENCE_SELECT}"
        )
        with self._cursor() as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        return dict(row)

    def add_route(
        self,
        client_id: uuid.UUID,
        route_id: uuid.UUID,
        *,
        query: str,
        plan: dict[str, Any],
        name: str | None = None,
        visit_overrides: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Store a checked plan verbatim.  The cap is enforced in the same
        statement, so two concurrent saves cannot both slip past it."""
        self.ensure_client(client_id)
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO saved_routes
                    (id, client_id, name, query, plan, visit_overrides,
                     created_at, updated_at)
                SELECT %s, %s, %s, %s, %s, %s, now(), now()
                WHERE (SELECT count(*) FROM saved_routes WHERE client_id = %s) < %s
                RETURNING id, created_at
                """,
                (
                    route_id,
                    client_id,
                    name,
                    query,
                    _adapt(plan, "plan"),
                    _adapt(visit_overrides, "visit_overrides"),
                    client_id,
                    self.max_routes,
                ),
            )
            row = cur.fetchone()
        if row is None:
            raise TooManyRoutes(self.max_routes)
        return {"id": row["id"], "created_at": row["created_at"]}

    def list_routes(
        self, client_id: uuid.UUID, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Newest first, without any heavy geometry.

        Only scalars are selected, so a saved polyline never crosses the wire."""
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT id, name, query, created_at,
                       CASE WHEN jsonb_typeof(plan->'points') = 'array'
                            THEN jsonb_array_length(plan->'points') ELSE 0 END
                            AS stop_count,
                       CASE WHEN jsonb_typeof(plan->'summary') = 'object'
                            THEN plan->'summary' END AS summary,
                       CASE WHEN jsonb_typeof(plan->'budget') = 'object'
                            THEN plan->'budget' END AS budget
                FROM saved_routes
                WHERE client_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (client_id, limit),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append({
                "id": row["id"],
                "name": row["name"],
                "query": row["query"],
                "created_at": row["created_at"],
                **route_metrics(row["stop_count"], row["summary"], row["budget"]),
            })
        return out

    def get_route(
        self, client_id: uuid.UUID, route_id: uuid.UUID
    ) -> dict[str, Any] | None:
        """The full record (plan and visit_overrides included), or ``None``."""
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {_ROUTE_DETAIL_SELECT} FROM saved_routes "
                "WHERE id = %s AND client_id = %s",
                (route_id, client_id),
            )
            row = cur.fetchone()
        return dict(row) if row is not None else None

    def rename_route(
        self, client_id: uuid.UUID, route_id: uuid.UUID, name: str
    ) -> dict[str, Any] | None:
        with self._cursor() as cur:
            cur.execute(
                f"UPDATE saved_routes SET name = %s, updated_at = now() "
                f"WHERE id = %s AND client_id = %s "
                f"RETURNING {_ROUTE_DETAIL_SELECT}",
                (name, route_id, client_id),
            )
            row = cur.fetchone()
        return dict(row) if row is not None else None

    def delete_route(self, client_id: uuid.UUID, route_id: uuid.UUID) -> bool:
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM saved_routes WHERE id = %s AND client_id = %s",
                (route_id, client_id),
            )
            return cur.rowcount > 0

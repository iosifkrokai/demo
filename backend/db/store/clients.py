"""Storage for the anonymous client entity.

One job: persist a client's preferences and saved routes and answer nothing
else. The connection, the lock and the mapping from a driver error to
:class:`StorageUnavailable` are the base repository's — this module is only the
queries and the shapes they return.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from db.models.client import ClientPreferences
from db.models.route import SavedRoute, SavedRouteSummary
from db.store.base import ConnectionFactory, PostgresRepository
from db.store.errors import TooManyRoutes
from db.store.mappers import model_from_row, route_metrics

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

_ROUTE_INSERT_SELECT = (
    "id, client_id, name, query, plan, visit_overrides, created_at, updated_at"
)


class ClientRepository(Protocol):
    """The surface clients_api depends on (a fake implements exactly this)."""

    def ensure_client(self, client_id: uuid.UUID) -> None: ...

    def get_preferences(self, client_id: uuid.UUID) -> ClientPreferences | None: ...

    def upsert_preferences(
        self, client_id: uuid.UUID, fields: dict[str, Any]
    ) -> ClientPreferences: ...

    def add_route(
        self,
        client_id: uuid.UUID,
        route_id: uuid.UUID,
        *,
        query: str,
        plan: dict[str, Any],
        name: str | None = None,
        visit_overrides: dict[str, int] | None = None,
    ) -> SavedRoute: ...

    def list_routes(
        self, client_id: uuid.UUID, limit: int = 50
    ) -> list[SavedRouteSummary]: ...

    def get_route(
        self, client_id: uuid.UUID, route_id: uuid.UUID
    ) -> SavedRoute | None: ...

    def rename_route(
        self, client_id: uuid.UUID, route_id: uuid.UUID, name: str
    ) -> SavedRoute | None: ...

    def delete_route(self, client_id: uuid.UUID, route_id: uuid.UUID) -> bool: ...

    def delete_client(self, client_id: uuid.UUID) -> bool: ...


def _adapt(value: Any, column: str) -> Any:
    """Adapt a Python value for psycopg.

    ``None`` stays SQL NULL; a dict/list is wrapped as jsonb, not a string.
    """
    if value is None:
        return None
    if column in ("plan", "visit_overrides", "visit_minutes_by_category"):
        return Jsonb(value)
    return value


class PostgresClientRepository(PostgresRepository):
    """A :class:`ClientRepository` backed by Postgres.

    The cap on saved routes is per-instance, so a test can lower it without
    touching the schema.
    """

    def __init__(
        self,
        connect: ConnectionFactory | None = None,
        *,
        max_routes: int = MAX_SAVED_ROUTES,
    ) -> None:
        super().__init__(connect)
        self.max_routes = max_routes

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

    def get_preferences(self, client_id: uuid.UUID) -> ClientPreferences | None:
        """The stored row, or ``None`` when nothing has been saved yet."""
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {_PREFERENCE_SELECT} FROM client_preferences "
                "WHERE client_id = %s",
                (client_id,),
            )
            row = cur.fetchone()
        return model_from_row(ClientPreferences, row) if row is not None else None

    def upsert_preferences(
        self, client_id: uuid.UUID, fields: dict[str, Any]
    ) -> ClientPreferences:
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
        assert row is not None
        return model_from_row(ClientPreferences, row)

    def add_route(
        self,
        client_id: uuid.UUID,
        route_id: uuid.UUID,
        *,
        query: str,
        plan: dict[str, Any],
        name: str | None = None,
        visit_overrides: dict[str, int] | None = None,
    ) -> SavedRoute:
        """Store a checked plan verbatim.  The cap is enforced in the same
        statement, so two concurrent saves cannot both slip past it."""
        self.ensure_client(client_id)
        with self._cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO saved_routes
                    (id, client_id, name, query, plan, visit_overrides,
                     created_at, updated_at)
                SELECT %s, %s, %s, %s, %s, %s, now(), now()
                WHERE (SELECT count(*) FROM saved_routes WHERE client_id = %s) < %s
                RETURNING {_ROUTE_INSERT_SELECT}
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
        return model_from_row(SavedRoute, row)

    def list_routes(
        self, client_id: uuid.UUID, limit: int = 50
    ) -> list[SavedRouteSummary]:
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
        return [
            SavedRouteSummary(
                id=row["id"],
                name=row["name"],
                query=row["query"],
                created_at=row["created_at"],
                **route_metrics(row["stop_count"], row["summary"], row["budget"]),
            )
            for row in rows
        ]

    def get_route(
        self, client_id: uuid.UUID, route_id: uuid.UUID
    ) -> SavedRoute | None:
        """The full record (plan and visit_overrides included), or ``None``."""
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {_ROUTE_DETAIL_SELECT} FROM saved_routes "
                "WHERE id = %s AND client_id = %s",
                (route_id, client_id),
            )
            row = cur.fetchone()
        return model_from_row(SavedRoute, row) if row is not None else None

    def rename_route(
        self, client_id: uuid.UUID, route_id: uuid.UUID, name: str
    ) -> SavedRoute | None:
        with self._cursor() as cur:
            cur.execute(
                f"UPDATE saved_routes SET name = %s, updated_at = now() "
                f"WHERE id = %s AND client_id = %s "
                f"RETURNING {_ROUTE_DETAIL_SELECT}",
                (name, route_id, client_id),
            )
            row = cur.fetchone()
        return model_from_row(SavedRoute, row) if row is not None else None

    def delete_route(self, client_id: uuid.UUID, route_id: uuid.UUID) -> bool:
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM saved_routes WHERE id = %s AND client_id = %s",
                (route_id, client_id),
            )
            return cur.rowcount > 0

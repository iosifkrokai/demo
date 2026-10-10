"""The account repository: users, their sessions and the places they visited.

The three tables are one aggregate — a visit exists because an account does, and
a session only ever points at one — so they share a repository rather than
splitting by table. Reads return the row models; a miss is ``None``, a Postgres
outage is :class:`StorageUnavailable`.

The admin CRUD over ``places`` used to live here too, on the strength of nothing
more than "an admin calls it"; it writes the place table, so it now lives on the
place repository. The counts that span users, routes and visits are a query, not
an aggregate, and live in :mod:`db.store.stats`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol

import psycopg

from db.models.user import AdminUser, User, VisitedPlace
from db.store.base import PostgresRepository
from db.store.columns import USER_COLUMNS, USER_SECRET_SELECT, USER_SELECT
from db.store.errors import EmailTaken
from db.store.mappers import model_from_row


class UserRepository(Protocol):
    """The surface the accounts and admin routers depend on (a fake implements this)."""

    def create_user(
        self,
        user_id: uuid.UUID,
        *,
        email: str,
        password_hash: str,
        display_name: str | None = None,
        role: str = "user",
        client_id: uuid.UUID | None = None,
    ) -> User: ...

    def get_user_by_email(self, email: str) -> User | None: ...

    def get_user(self, user_id: uuid.UUID) -> User | None: ...

    def link_client(self, user_id: uuid.UUID, client_id: uuid.UUID) -> bool: ...

    def touch_login(self, user_id: uuid.UUID) -> None: ...

    def create_session(
        self, token_hash: str, user_id: uuid.UUID, expires_at: datetime
    ) -> None: ...

    def get_session_user(self, token_hash: str) -> User | None: ...

    def delete_session(self, token_hash: str) -> None: ...

    def list_users(
        self, *, q: str = "", limit: int = 50, offset: int = 0
    ) -> tuple[list[AdminUser], int]: ...

    def count_admins(self) -> int: ...

    def update_user(
        self,
        user_id: uuid.UUID,
        *,
        role: str | None = None,
        display_name: str | None = None,
        display_name_set: bool = False,
    ) -> User | None: ...

    def delete_user(self, user_id: uuid.UUID) -> bool: ...

    def list_visited(self, user_id: uuid.UUID) -> list[VisitedPlace]: ...

    def mark_visited(
        self, user_id: uuid.UUID, place_id: int
    ) -> VisitedPlace | None: ...

    def mark_visited_many(
        self, user_id: uuid.UUID, place_ids: list[int]
    ) -> list[int]: ...

    def unmark_visited(self, user_id: uuid.UUID, place_id: int) -> bool: ...


def _selected(alias: str) -> str:
    """`u.id, u.email …` for the session and admin joins."""
    return ", ".join(f"{alias}.{column}" for column in USER_COLUMNS)


class PostgresUserRepository(PostgresRepository):
    """The accounts, sessions and visits, read and written through one connection."""

    def create_user(
        self,
        user_id: uuid.UUID,
        *,
        email: str,
        password_hash: str,
        display_name: str | None = None,
        role: str = "user",
        client_id: uuid.UUID | None = None,
    ) -> User:
        """Insert an account. Raises :class:`EmailTaken` on a duplicate address.

        An anonymous client is adopted only when nobody else already owns it.
        """
        with self._cursor() as cur:
            if client_id is not None:
                cur.execute(
                    """
                    INSERT INTO clients (id, created_at, last_seen_at)
                    VALUES (%s, now(), now())
                    ON CONFLICT (id) DO UPDATE SET last_seen_at = now()
                    """,
                    (client_id,),
                )
                cur.execute(
                    "SELECT 1 FROM users WHERE client_id = %s", (client_id,)
                )
                if cur.fetchone() is not None:
                    client_id = None
            try:
                cur.execute(
                    f"""
                    INSERT INTO users
                        (id, email, password_hash, display_name, role, client_id,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, now(), now())
                    RETURNING {USER_SELECT}
                    """,
                    (
                        user_id,
                        email,
                        password_hash,
                        display_name,
                        role,
                        client_id,
                    ),
                )
                row = cur.fetchone()
            except psycopg.errors.UniqueViolation as exc:
                raise EmailTaken(email) from exc
        assert row is not None
        return model_from_row(User, row)

    def get_user_by_email(self, email: str) -> User | None:
        """The account *including* ``password_hash`` — the login lookup only."""
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {USER_SECRET_SELECT} FROM users WHERE email = %s",
                (email,),
            )
            row = cur.fetchone()
        return model_from_row(User, row) if row is not None else None

    def get_user(self, user_id: uuid.UUID) -> User | None:
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {USER_SELECT} FROM users WHERE id = %s", (user_id,)
            )
            row = cur.fetchone()
        return model_from_row(User, row) if row is not None else None

    def link_client(self, user_id: uuid.UUID, client_id: uuid.UUID) -> bool:
        """Give the account the anonymous client, if neither side already has one."""
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO clients (id, created_at, last_seen_at)
                VALUES (%s, now(), now())
                ON CONFLICT (id) DO UPDATE SET last_seen_at = now()
                """,
                (client_id,),
            )
            cur.execute(
                """
                UPDATE users SET client_id = %s, updated_at = now()
                WHERE id = %s
                  AND client_id IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM users u2
                      WHERE u2.client_id = %s AND u2.id <> %s
                  )
                """,
                (client_id, user_id, client_id, user_id),
            )
            return cur.rowcount > 0

    def touch_login(self, user_id: uuid.UUID) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE users SET last_login_at = now() WHERE id = %s", (user_id,)
            )

    def create_session(
        self, token_hash: str, user_id: uuid.UUID, expires_at: datetime
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_sessions (token_hash, user_id, expires_at)
                VALUES (%s, %s, %s)
                """,
                (token_hash, user_id, expires_at),
            )

    def get_session_user(self, token_hash: str) -> User | None:
        """The owner of a live session, or ``None`` if unknown or expired."""
        with self._cursor() as cur:
            cur.execute(
                f"""
                SELECT {_selected("u")}
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = %s AND s.expires_at > now()
                """,
                (token_hash,),
            )
            row = cur.fetchone()
        return model_from_row(User, row) if row is not None else None

    def delete_session(self, token_hash: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM user_sessions WHERE token_hash = %s", (token_hash,)
            )

    def list_users(
        self, *, q: str = "", limit: int = 50, offset: int = 0
    ) -> tuple[list[AdminUser], int]:
        """Newest first, with the two counts an admin asks about."""
        pattern = f"%{q}%"
        where = "(u.email ILIKE %s OR u.display_name ILIKE %s)"
        with self._cursor() as cur:
            cur.execute(
                f"""
                SELECT {_selected("u")},
                       (SELECT count(*) FROM saved_routes r
                        WHERE r.client_id = u.client_id) AS saved_routes,
                       (SELECT count(*) FROM visited_places v
                        WHERE v.user_id = u.id) AS visited
                FROM users u
                WHERE {where}
                ORDER BY u.created_at DESC, u.id DESC
                LIMIT %s OFFSET %s
                """,
                (pattern, pattern, limit, offset),
            )
            rows = [model_from_row(AdminUser, r) for r in cur.fetchall()]
            cur.execute(f"SELECT count(*) AS n FROM users u WHERE {where}",
                        (pattern, pattern))
            total = int(cur.fetchone()["n"])
        return rows, total

    def count_admins(self) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM users WHERE role = 'admin'")
            return int(cur.fetchone()["n"])

    def update_user(
        self,
        user_id: uuid.UUID,
        *,
        role: str | None = None,
        display_name: str | None = None,
        display_name_set: bool = False,
    ) -> User | None:
        sets: list[str] = []
        params: list[Any] = []
        if role is not None:
            sets.append("role = %s")
            params.append(role)
        if display_name_set:
            sets.append("display_name = %s")
            params.append(display_name)
        if not sets:
            return self.get_user(user_id)
        sets.append("updated_at = now()")
        params.append(user_id)
        with self._cursor() as cur:
            cur.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = %s "
                f"RETURNING {USER_SELECT}",
                tuple(params),
            )
            row = cur.fetchone()
        return model_from_row(User, row) if row is not None else None

    def delete_user(self, user_id: uuid.UUID) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            return cur.rowcount > 0

    def list_visited(self, user_id: uuid.UUID) -> list[VisitedPlace]:
        """The marks themselves, newest first — the place data is the place repo's."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT user_id, place_id, visited_at FROM visited_places "
                "WHERE user_id = %s ORDER BY visited_at DESC, place_id",
                (user_id,),
            )
            return [model_from_row(VisitedPlace, row) for row in cur.fetchall()]

    def mark_visited(
        self, user_id: uuid.UUID, place_id: int
    ) -> VisitedPlace | None:
        """Idempotent mark; ``None`` when the place does not exist."""
        with self._cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO visited_places (user_id, place_id)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id, place_id) DO UPDATE
                        SET visited_at = visited_places.visited_at
                    RETURNING user_id, place_id, visited_at
                    """,
                    (user_id, place_id),
                )
            except psycopg.errors.ForeignKeyViolation:
                return None
            row = cur.fetchone()
        return model_from_row(VisitedPlace, row) if row is not None else None

    def mark_visited_many(
        self, user_id: uuid.UUID, place_ids: list[int]
    ) -> list[int]:
        """Mark several places at once; unknown ids are simply skipped."""
        if not place_ids:
            return []
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO visited_places (user_id, place_id)
                SELECT %s, p.id
                FROM unnest(%s::int[]) AS p(id)
                JOIN places pl ON pl.id = p.id
                ON CONFLICT (user_id, place_id) DO NOTHING
                RETURNING place_id
                """,
                (user_id, list(place_ids)),
            )
            return [int(r["place_id"]) for r in cur.fetchall()]

    def unmark_visited(self, user_id: uuid.UUID, place_id: int) -> bool:
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM visited_places WHERE user_id = %s AND place_id = %s",
                (user_id, place_id),
            )
            return cur.rowcount > 0

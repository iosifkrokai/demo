"""Storage for accounts, sessions, visits and the admin surface (spec 005).

Same shape as :mod:`agent.clients_store`: one repository, one job — persist and
read, and answer nothing else. It keeps the promises the spec depends on:

* **Degradation is typed, not silent.** A read that finds nothing returns ``None``
  (or an empty list); Postgres being unreachable or erroring raises
  :class:`StorageUnavailable`, which the HTTP layer turns into ``503
  storage_unavailable``. No method returns an empty value to mask an outage.

* **Secrets never come back.** The password hash stays inside this module: it is
  read only by :meth:`get_user_by_email` (for login) and never selected by any
  query that feeds an API response. Session rows hold ``sha256(token)``.

* **The curated-category guard is respected, not bypassed blindly.** Editing a
  place's ``category`` sets ``category_source='curated'`` and opts the current
  transaction in (``grodno.allow_curated_category_change``), because otherwise the
  trigger from migration 0004 silently reverts the write.

The place payload itself is not re-invented here: rows are read with the same
column set the catalogue uses and handed to :func:`agent.places.place_payload`, so
a card printed by the admin panel and one printed by the «все точки» tab cannot
disagree about the same place.
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

from .config import settings

log = logging.getLogger(__name__)

#: Everything a place card prints — kept identical to ``agent/places._COLS`` so
#: ``place_payload`` accepts these rows.
PLACE_COLS = (
    "id, source_url, name, category, town, district, lat, lon, "
    "visit_minutes, opening_hours, blurb, fun_fact, fun_facts, links, "
    "ticket_price, photo_url, photo_author, photo_license, photo_source"
)

#: A user row as the API may see it. ``password_hash`` is deliberately absent —
#: it is selected only by the login lookup, through a separate column list.
USER_COLS = (
    "id, email, display_name, role, client_id, created_at, updated_at, "
    "last_login_at"
)

#: Columns a partial update of a place may touch (source_url included: an admin
#: may correct a wrong provenance link).
PLACE_WRITE_COLS = (
    "name",
    "lat",
    "lon",
    "source_url",
    "category",
    "town",
    "district",
    "blurb",
    "fun_fact",
    "visit_minutes",
    "opening_hours",
    "ticket_price",
)

#: The in-process default allowed for LIST endpoints, as a backstop, not a view.
MAX_LIST_LIMIT = 500


class StorageUnavailable(RuntimeError):
    """The account store could not reach Postgres (→ ``503 storage_unavailable``)."""


class EmailTaken(RuntimeError):
    """The requested email already belongs to an account (→ ``409 email_taken``)."""


class DuplicateSource(RuntimeError):
    """A place with this ``source_url`` already exists (→ ``409 source_taken``)."""


class AccountRepository(Protocol):
    """The surface ``accounts_api`` depends on (a fake implements exactly this)."""

    def create_user(
        self,
        user_id: uuid.UUID,
        *,
        email: str,
        password_hash: str,
        display_name: str | None = None,
        role: str = "user",
        client_id: uuid.UUID | None = None,
    ) -> dict[str, Any]: ...

    def get_user_by_email(self, email: str) -> dict[str, Any] | None: ...

    def get_user(self, user_id: uuid.UUID) -> dict[str, Any] | None: ...

    def link_client(self, user_id: uuid.UUID, client_id: uuid.UUID) -> bool: ...

    def touch_login(self, user_id: uuid.UUID) -> None: ...

    def create_session(
        self, token_hash: str, user_id: uuid.UUID, expires_at: Any
    ) -> None: ...

    def get_session_user(self, token_hash: str) -> dict[str, Any] | None: ...

    def delete_session(self, token_hash: str) -> None: ...

    def list_users(
        self, *, q: str = "", limit: int = 50, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]: ...

    def count_admins(self) -> int: ...

    def update_user(
        self,
        user_id: uuid.UUID,
        *,
        role: str | None = None,
        display_name: str | None = None,
        display_name_set: bool = False,
    ) -> dict[str, Any] | None: ...

    def delete_user(self, user_id: uuid.UUID) -> bool: ...

    def list_visited(self, user_id: uuid.UUID) -> list[dict[str, Any]]: ...

    def mark_visited(
        self, user_id: uuid.UUID, place_id: int
    ) -> dict[str, Any] | None: ...

    def mark_visited_many(
        self, user_id: uuid.UUID, place_ids: list[int]
    ) -> list[int]: ...

    def unmark_visited(self, user_id: uuid.UUID, place_id: int) -> bool: ...

    def list_places(
        self, *, q: str = "", category: str = "", limit: int = 50, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]: ...

    def get_place(self, place_id: int) -> dict[str, Any] | None: ...

    def create_place(self, fields: dict[str, Any]) -> dict[str, Any]: ...

    def update_place(
        self, place_id: int, fields: dict[str, Any]
    ) -> dict[str, Any] | None: ...

    def delete_place(self, place_id: int) -> bool: ...

    def stats(self) -> dict[str, int]: ...


def default_connect() -> psycopg.Connection:
    """One autocommit connection through the DSN from config.py.

    ``connect_timeout`` is short on purpose: when the database is down the request
    must fail fast into ``503 storage_unavailable``, not hang.
    """
    return psycopg.connect(settings.DSN, autocommit=True, connect_timeout=3)


class PostgresAccountRepository:
    """An :class:`AccountRepository` backed by Postgres.

    The connection is created lazily and reused; a broken one is dropped and
    reopened. A lock serialises access, because FastAPI runs sync endpoints in a
    thread pool and a psycopg connection is not safe to share concurrently.
    """

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection] | None = None,
    ) -> None:
        self._connect = connect or default_connect
        self._conn: psycopg.Connection | None = None
        self._lock = threading.Lock()

    # ── connection plumbing ────────────────────────────────────────────────

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

    @contextmanager
    def _tx_cursor(self) -> Iterator[Any]:
        """A cursor inside an explicit transaction (needed for ``SET LOCAL``).

        The connection is autocommit, so ``set_config(..., is_local=true)`` would
        be a no-op outside a transaction — this opens one for the curated-guard
        opt-in and rolls it back on failure.
        """
        try:
            with self._lock:
                conn = self._connection()
                with conn.transaction():
                    with conn.cursor(row_factory=dict_row) as cur:
                        yield cur
        except psycopg.Error as exc:
            self._drop()
            raise StorageUnavailable(str(exc)) from exc

    def close(self) -> None:
        with self._lock:
            self._drop()

    # ── users ──────────────────────────────────────────────────────────────

    def create_user(
        self,
        user_id: uuid.UUID,
        *,
        email: str,
        password_hash: str,
        display_name: str | None = None,
        role: str = "user",
        client_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Insert an account. Raises :class:`EmailTaken` on a duplicate address.

        The anonymous client is adopted only when it exists and is not already
        owned by someone else — a registration never steals another account's
        client, and never fails because of it.
        """
        with self._cursor() as cur:
            if client_id is not None:
                # The clients row may not exist yet (the browser minted the id but
                # never saved anything), and the FK below requires it.
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
                    client_id = None  # already adopted elsewhere
            try:
                cur.execute(
                    f"""
                    INSERT INTO users
                        (id, email, password_hash, display_name, role, client_id,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, now(), now())
                    RETURNING {USER_COLS}
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
        return dict(row)

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        """The full row *including* ``password_hash`` — the login lookup only."""
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {USER_COLS}, password_hash FROM users WHERE email = %s",
                (email,),
            )
            row = cur.fetchone()
        return dict(row) if row is not None else None

    def get_user(self, user_id: uuid.UUID) -> dict[str, Any] | None:
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {USER_COLS} FROM users WHERE id = %s", (user_id,)
            )
            row = cur.fetchone()
        return dict(row) if row is not None else None

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

    # ── sessions ───────────────────────────────────────────────────────────

    def create_session(
        self, token_hash: str, user_id: uuid.UUID, expires_at: Any
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_sessions (token_hash, user_id, expires_at)
                VALUES (%s, %s, %s)
                """,
                (token_hash, user_id, expires_at),
            )

    def get_session_user(self, token_hash: str) -> dict[str, Any] | None:
        """The owner of a live session, or ``None`` if unknown or expired."""
        with self._cursor() as cur:
            cur.execute(
                f"""
                SELECT {', '.join('u.' + c for c in USER_COLS.split(', '))}
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = %s AND s.expires_at > now()
                """,
                (token_hash,),
            )
            row = cur.fetchone()
        return dict(row) if row is not None else None

    def delete_session(self, token_hash: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM user_sessions WHERE token_hash = %s", (token_hash,)
            )

    # ── admin: users ───────────────────────────────────────────────────────

    def list_users(
        self, *, q: str = "", limit: int = 50, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """Newest first, with the two counts an admin asks about."""
        pattern = f"%{q}%"
        where = "(u.email ILIKE %s OR u.display_name ILIKE %s)"
        with self._cursor() as cur:
            cur.execute(
                f"""
                SELECT {', '.join('u.' + c for c in USER_COLS.split(', '))},
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
            rows = [dict(r) for r in cur.fetchall()]
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
    ) -> dict[str, Any] | None:
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
                f"RETURNING {USER_COLS}",
                tuple(params),
            )
            row = cur.fetchone()
        return dict(row) if row is not None else None

    def delete_user(self, user_id: uuid.UUID) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            return cur.rowcount > 0

    # ── visits ─────────────────────────────────────────────────────────────

    def list_visited(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        place_cols = ", ".join(
            f"p.{c.strip()}" for c in PLACE_COLS.split(",")
        )
        with self._cursor() as cur:
            cur.execute(
                f"""
                SELECT {place_cols}, v.visited_at
                FROM visited_places v
                JOIN places p ON p.id = v.place_id
                WHERE v.user_id = %s
                ORDER BY v.visited_at DESC, p.id
                """,
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def mark_visited(
        self, user_id: uuid.UUID, place_id: int
    ) -> dict[str, Any] | None:
        """Idempotent mark; ``None`` when the place does not exist."""
        with self._cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO visited_places (user_id, place_id)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id, place_id) DO UPDATE
                        SET visited_at = visited_places.visited_at
                    RETURNING place_id, visited_at
                    """,
                    (user_id, place_id),
                )
            except psycopg.errors.ForeignKeyViolation:
                return None
            row = cur.fetchone()
        return dict(row) if row is not None else None

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

    # ── admin: places ──────────────────────────────────────────────────────

    def list_places(
        self, *, q: str = "", category: str = "", limit: int = 50, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        pattern = f"%{q}%"
        where = (
            "(%s = '' OR name ILIKE %s OR town ILIKE %s OR district ILIKE %s) "
            "AND (%s = '' OR category = %s)"
        )
        args = (q, pattern, pattern, pattern, category, category)
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {PLACE_COLS} FROM places WHERE {where} "
                "ORDER BY name, id LIMIT %s OFFSET %s",
                (*args, limit, offset),
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute(f"SELECT count(*) AS n FROM places WHERE {where}", args)
            total = int(cur.fetchone()["n"])
        return rows, total

    def get_place(self, place_id: int) -> dict[str, Any] | None:
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {PLACE_COLS} FROM places WHERE id = %s", (place_id,)
            )
            row = cur.fetchone()
        return dict(row) if row is not None else None

    def create_place(self, fields: dict[str, Any]) -> dict[str, Any]:
        cols = [c for c in PLACE_WRITE_COLS if c in fields]
        # A category authored by an admin is theirs: mark it curated so the guard
        # from migration 0004 protects it from later dataset writes.
        extra_cols = ["category_source"] if "category" in cols else []
        values_sql = ", ".join(["%s"] * (len(cols) + len(extra_cols)))
        params = [fields[c] for c in cols]
        if extra_cols:
            params.append("curated")
        with self._cursor() as cur:
            try:
                cur.execute(
                    f"INSERT INTO places ({', '.join([*cols, *extra_cols])}) "
                    f"VALUES ({values_sql}) RETURNING {PLACE_COLS}",
                    tuple(params),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise DuplicateSource(str(fields.get("source_url"))) from exc
            row = cur.fetchone()
        return dict(row)

    def update_place(
        self, place_id: int, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        cols = [c for c in PLACE_WRITE_COLS if c in fields]
        if not cols:
            return self.get_place(place_id)
        sets = [f"{c} = %s" for c in cols]
        params: list[Any] = [fields[c] for c in cols]
        if "category" in cols:
            # Curated wins over the dataset; record that and opt this transaction
            # into the guard, or the trigger below silently reverts the change.
            sets.append("category_source = 'curated'")
        params.append(place_id)
        with self._tx_cursor() as cur:
            if "category" in cols:
                cur.execute(
                    "SELECT set_config('grodno.allow_curated_category_change', "
                    "'on', true)"
                )
            try:
                cur.execute(
                    f"UPDATE places SET {', '.join(sets)} WHERE id = %s "
                    f"RETURNING {PLACE_COLS}",
                    tuple(params),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise DuplicateSource(str(fields.get("source_url"))) from exc
            row = cur.fetchone()
        return dict(row) if row is not None else None

    def delete_place(self, place_id: int) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM places WHERE id = %s", (place_id,))
            return cur.rowcount > 0

    # ── dashboard ──────────────────────────────────────────────────────────

    def stats(self) -> dict[str, int]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT (SELECT count(*) FROM users)                AS users,
                       (SELECT count(*) FROM users
                        WHERE role = 'admin')                      AS admins,
                       (SELECT count(*) FROM places)               AS places,
                       (SELECT count(*) FROM visited_places)       AS visited,
                       (SELECT count(*) FROM saved_routes)         AS saved_routes
                """
            )
            row = cur.fetchone()
        return {k: int(v) for k, v in dict(row).items()}


def place_payloads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn place rows into the shared card payload (one import site)."""
    from .places import place_payload

    return [place_payload(row) for row in rows]

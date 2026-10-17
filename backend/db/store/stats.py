"""The numbers the admin header shows, in one query."""

from __future__ import annotations

from db.store.base import PostgresRepository
from db.store.errors import StorageUnavailableError


class PostgresStatsRepository(PostgresRepository):
    """Read-only counts over the tables the admin panel summarises."""

    def stats(self) -> dict[str, int]:
        """One row of ``count(*)`` per table the header names."""
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT (SELECT count(*) FROM users)                AS users,
                       (SELECT count(*) FROM users
                        WHERE role = 'admin')                      AS admins,
                       (SELECT count(*) FROM places)               AS places,
                       (SELECT count(*) FROM visited_places)       AS visited
                """
            )
            row = cur.fetchone()
        if row is None:
            raise StorageUnavailableError("stats query returned no row")
        return {key: int(value) for key, value in row.items()}

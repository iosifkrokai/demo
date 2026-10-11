"""The numbers the admin header shows, in one query.

They count users, places, visits and routes — four tables with no aggregate in
common — so they belong to no single repository. Keeping them here keeps the
``count(*)`` from being scattered across the aggregates that own the tables.
"""

from __future__ import annotations

from typing import Protocol

from db.store.base import PostgresRepository


class StatsRepository(Protocol):
    """The admin counts the stats route depends on (a fake implements this)."""

    def stats(self) -> dict[str, int]: ...


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
                       (SELECT count(*) FROM visited_places)       AS visited,
                       (SELECT count(*) FROM saved_routes)         AS saved_routes
                """
            )
            row = cur.fetchone()
        assert row is not None
        return {key: int(value) for key, value in row.items()}

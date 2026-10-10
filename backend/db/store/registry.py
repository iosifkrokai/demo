"""What a logic layer is handed instead of a connection.

A `Pipeline` used to take a `psycopg.Connection` and thread it through nineteen
call sites in five modules. It takes this instead: the connection stays inside
the repository that owns it, and nothing above `db/store` can run a query it did
not ask for.
"""

from __future__ import annotations

from dataclasses import dataclass

from db.store.areas import PostgresAreaRepository
from db.store.places import PostgresPlaceRepository


@dataclass(frozen=True)
class Repositories:
    """One instance per request-scoped component, wired once at startup.

    Frozen so a request cannot swap a repository mid-flight; the repositories
    themselves are mutable, because each owns a connection it reconnects lazily.
    """

    places: PostgresPlaceRepository
    areas: PostgresAreaRepository

    def close(self) -> None:
        self.places.close()
        self.areas.close()

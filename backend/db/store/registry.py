"""What a logic layer is handed instead of a connection.

A `Pipeline` used to take a `psycopg.Connection` and thread it through nineteen
call sites in five modules. It takes this instead: the connection stays inside
the repository that owns it, and nothing above `db/store` can run a query it did
not ask for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from db.store.areas import PostgresAreaRepository
from db.store.clients import PostgresClientRepository
from db.store.places import PostgresPlaceRepository
from db.store.stats import PostgresStatsRepository
from db.store.users import PostgresUserRepository


@dataclass(frozen=True)
class Repositories:
    """One instance per request-scoped component, wired once at startup.

    Frozen so a request cannot swap a repository mid-flight; the repositories
    themselves are mutable, because each owns a connection it reconnects lazily.
    The account, client and stats repositories default to real ones so a
    planner-only construction — `Repositories(places=…, areas=…)` — stays a
    one-liner; a test that needs a fake passes it explicitly.
    """

    places: PostgresPlaceRepository
    areas: PostgresAreaRepository
    users: PostgresUserRepository = field(default_factory=PostgresUserRepository)
    stats: PostgresStatsRepository = field(default_factory=PostgresStatsRepository)
    clients: PostgresClientRepository = field(default_factory=PostgresClientRepository)

    def close(self) -> None:
        for repo in (self.places, self.areas, self.users, self.stats, self.clients):
            repo.close()

"""What a logic layer is handed instead of a connection.

The connection stays inside the repository that owns it, so nothing above
`db/store` can run a query it did not ask for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from db.store.areas import PostgresAreaRepository
from db.store.itineraries import PostgresItineraryRepository
from db.store.places import PostgresPlaceRepository
from db.store.stats import PostgresStatsRepository
from db.store.taxonomy import PostgresCategoryRepository
from db.store.users import PostgresUserRepository


@dataclass(frozen=True)
class Repositories:
    """One instance per request-scoped component, wired once at startup.

    Frozen so a request cannot swap a repository mid-flight; the repositories
    themselves are mutable, because each owns a connection it reconnects lazily.
    The account and stats repositories default to real ones so a planner-only
    construction — `Repositories(places=…, areas=…)` — stays a one-liner; a test
    that needs a fake passes it explicitly.
    """

    places: PostgresPlaceRepository
    areas: PostgresAreaRepository
    taxonomy: PostgresCategoryRepository = field(default_factory=PostgresCategoryRepository)
    itineraries: PostgresItineraryRepository = field(default_factory=PostgresItineraryRepository)
    users: PostgresUserRepository = field(default_factory=PostgresUserRepository)
    stats: PostgresStatsRepository = field(default_factory=PostgresStatsRepository)

    def close(self) -> None:
        for repo in (
            self.places,
            self.areas,
            self.taxonomy,
            self.itineraries,
            self.users,
            self.stats,
        ):
            repo.close()

"""What a logic layer is handed instead of a connection."""

from __future__ import annotations

from dataclasses import dataclass, field

from db.store.areas import PostgresAreaRepository
from db.store.datasets import PostgresDatasetRepository
from db.store.itineraries import PostgresItineraryRepository
from db.store.places import PostgresPlaceRepository
from db.store.stats import PostgresStatsRepository
from db.store.taxonomy import PostgresCategoryRepository
from db.store.threads import PostgresThreadRepository
from db.store.users import PostgresUserRepository


@dataclass(frozen=True)
class Repositories:
    """One instance per request-scoped component, wired once at startup."""

    places: PostgresPlaceRepository
    areas: PostgresAreaRepository
    taxonomy: PostgresCategoryRepository = field(default_factory=PostgresCategoryRepository)
    itineraries: PostgresItineraryRepository = field(default_factory=PostgresItineraryRepository)
    users: PostgresUserRepository = field(default_factory=PostgresUserRepository)
    stats: PostgresStatsRepository = field(default_factory=PostgresStatsRepository)
    threads: PostgresThreadRepository = field(default_factory=PostgresThreadRepository)
    datasets: PostgresDatasetRepository = field(default_factory=PostgresDatasetRepository)

    def close(self) -> None:
        for repo in (
            self.places,
            self.areas,
            self.taxonomy,
            self.itineraries,
            self.users,
            self.stats,
            self.threads,
            self.datasets,
        ):
            repo.close()

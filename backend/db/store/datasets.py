"""The data version the seed stamped — read for the measurement cache.

``python -m db.seed`` writes one row into ``data_versions``: the digest of the
committed datasets (``db.seed.version``). The planner reads it to key its
measurement cache, so a re-seed that moves a stop invalidates the numbers computed
against rows that no longer say the same thing.

One indexed row is cheap, but the request path should not pay for it on every
measurement: the repository holds what it read for a short window. The hold is
shorter than the measurement cache's own TTL, so a re-seed under a running process
is noticed within it rather than only at the next deploy.
"""

from __future__ import annotations

from typing import Protocol

from core.cache import TtlLru
from db.store.base import ConnectionFactory, PostgresRepository

# The singleton row ``python -m db.seed`` writes: one projection, one version.
DATA_VERSION_ROW = "seed"
# How long a process trusts the version it read. Short on purpose — seeing a
# re-seed promptly is the whole point of carrying a version at all.
VERSION_TTL_S = 60
_KEY = "data_version"


class DatasetVersionRepository(Protocol):
    """The one read the planner needs from ``data_versions``."""

    def version(self) -> str | None: ...


class PostgresDatasetRepository(PostgresRepository):
    """Reads the projected data version, held for a short window."""

    def __init__(
        self, connect: ConnectionFactory | None = None, *, ttl_s: int = VERSION_TTL_S
    ) -> None:
        super().__init__(connect)
        self._hold = TtlLru(maxsize=1, ttl_s=ttl_s)

    def version(self) -> str | None:
        """The stamped version, or None before the seed has written one.

        Raises :class:`StorageUnavailableError` like any other read when the
        database is down or the table is not migrated in; the caller treats a
        version it cannot read as "no version", not as a failed plan.
        """
        held = self._hold.get(_KEY)
        if held is not None:
            return held or None
        with self._cursor() as cur:
            cur.execute("SELECT version FROM data_versions WHERE name = %s", (DATA_VERSION_ROW,))
            row = cur.fetchone()
        # A missing row is cached as "" (held) rather than re-queried: the version
        # simply has not been stamped yet, and the window still refreshes it.
        value = str(row["version"]) if row else ""
        self._hold.put(_KEY, value)
        return value or None

"""The data version the seed stamped — read for the measurement cache."""

from __future__ import annotations

from core.cache import TtlLru
from db.store.base import ConnectionFactory, PostgresRepository

DATA_VERSION_ROW = "seed"
VERSION_TTL_S = 60
_KEY = "data_version"


class PostgresDatasetRepository(PostgresRepository):
    """Reads the projected data version, held for a short window."""

    def __init__(
        self, connect: ConnectionFactory | None = None, *, ttl_s: int = VERSION_TTL_S
    ) -> None:
        super().__init__(connect)
        self._hold = TtlLru(maxsize=1, ttl_s=ttl_s)

    def version(self) -> str | None:
        """The stamped version, or None before the seed has written one."""
        held = self._hold.get(_KEY)
        if held is not None:
            return held or None
        with self._cursor() as cur:
            cur.execute("SELECT version FROM data_versions WHERE name = %s", (DATA_VERSION_ROW,))
            row = cur.fetchone()
        value = str(row["version"]) if row else ""
        self._hold.put(_KEY, value)
        return value or None

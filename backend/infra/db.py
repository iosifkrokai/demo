"""The one place that opens a Postgres connection."""

from __future__ import annotations

import psycopg

from core.config import settings


def connect(
    dsn: str | None = None,
    *,
    autocommit: bool = False,
    timeout: float | None = 3.0,
) -> psycopg.Connection:
    """Open a Postgres connection through the configured DSN.

    ``autocommit`` and ``timeout`` are passed straight through; libpq wants an
    int for ``connect_timeout``. An explicit ``dsn`` wins over the configured one.
    """
    if timeout is None:
        return psycopg.connect(dsn or settings.DSN, autocommit=autocommit)
    return psycopg.connect(
        dsn or settings.DSN, autocommit=autocommit, connect_timeout=int(timeout)
    )

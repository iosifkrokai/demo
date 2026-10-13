"""The one place that opens a Postgres connection."""

from __future__ import annotations

import psycopg
from psycopg import sql

from core.config import settings

WORD_SIMILARITY_THRESHOLD = 0.45
_SESSION_SETUP = sql.SQL("SET pg_trgm.word_similarity_threshold = {}").format(
    sql.Literal(WORD_SIMILARITY_THRESHOLD)
)


def connect(
    dsn: str | None = None,
    *,
    autocommit: bool = False,
    timeout: float | None = 3.0,
) -> psycopg.Connection:
    """Open a Postgres connection through the configured DSN, session GUCs applied."""
    if timeout is None:
        conn = psycopg.connect(dsn or settings.DSN, autocommit=autocommit)
    else:
        conn = psycopg.connect(
            dsn or settings.DSN, autocommit=autocommit, connect_timeout=int(timeout)
        )
    try:
        conn.execute(_SESSION_SETUP)
        if not autocommit:
            conn.commit()
    except Exception:
        conn.close()
        raise
    return conn

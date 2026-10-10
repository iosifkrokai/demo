"""The tools' connection handling, built on :func:`infra.db.connect`."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from infra import db as infra_db

log = logging.getLogger(__name__)

DB_TIMEOUT_S = 3.0


def _connect() -> Any:
    """Open a short-lived psycopg connection."""
    return infra_db.connect(timeout=DB_TIMEOUT_S)


def _db_search_rows(
    db: Any,
    *,
    query: str,
    limit: int,
    near: tuple[float, float, float] | None,
) -> list[dict]:
    """Rows from the shared retrieval layer — no SQL is ever model-supplied.

    Reuses the same signals the deterministic pipeline uses.
    """
    from db.store import search as search_mod

    if near is not None:
        lat, lon, radius_m = near
        return search_mod.nearby_places(db, lat, lon, radius_km=radius_m / 1000.0, limit=limit)
    return search_mod._keyword_search(db, query, limit=limit)


def _db_place_row(db: Any, place_id: int) -> dict | None:
    """One place row by id, or None."""
    from db.store import search as search_mod

    rows = search_mod.fetch_points_by_ids(db, [place_id])
    return rows[0] if rows else None


def _db_area_rows(db: Any, term: str, limit: int) -> list[dict]:
    """Area rows whose code/name/aliases match `term` (DB fallback shim)."""
    like = f"%{term.strip()}%"
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT code, name_ru, name_en, kind
              FROM areas
             WHERE name_ru ILIKE %s
                OR name_en ILIKE %s
                OR code ILIKE %s
                OR EXISTS (
                       SELECT 1 FROM unnest(aliases) AS a
                        WHERE a ILIKE %s
                   )
             ORDER BY kind, code
             LIMIT %s
            """,
            (like, like, like, like, limit),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def _fetch(db: Any, fn: Callable[[Any], Any]) -> tuple[Any, str | None, str | None]:
    """Run `fn(conn)` on `db` or a short-lived connection. Never raises.

    Returns ``(value, error_code, message)``; `value` is None on failure.
    """
    from agent.tools import ERR_DB_UNAVAILABLE

    conn = db
    close = False
    try:
        if conn is None:
            conn = _connect()
            close = True
        return fn(conn), None, None
    except ImportError as exc:
        log.warning("tool: db driver unavailable: %s", exc)
        return None, ERR_DB_UNAVAILABLE, "database driver not available"
    except Exception as exc:
        log.warning("tool: db access failed: %s", exc)
        return None, ERR_DB_UNAVAILABLE, "database not reachable"
    finally:
        if close and conn is not None:
            with suppress(Exception):
                conn.close()


# Re-exported for the tool modules that own the SQL callers.
__all__ = ["_db_area_rows", "_db_place_row", "_db_search_rows", "_fetch"]

"""The area repository: the `areas` table, read for territory name resolution."""

from __future__ import annotations

import logging

from db.models.area import Area
from db.store.base import PostgresRepository
from db.store.columns import AREA_SELECT
from db.store.mappers import area_from_row

log = logging.getLogger(__name__)


class PostgresAreaRepository(PostgresRepository):
    """The territories the system can resolve a word to.

    This query used to live in `agent/tools/_db.py` — SQL inside the LLM layer,
    with no place to put it. It is a read of `areas` like any other, so it lives
    with the other reads.
    """

    def search(self, term: str, limit: int = 8) -> list[Area]:
        """Areas whose code, name or an alias contains `term`, districts first."""
        like = f"%{term.strip()}%"
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {AREA_SELECT} FROM areas "
                "WHERE name_ru ILIKE %s OR name_en ILIKE %s OR code ILIKE %s "
                "   OR EXISTS (SELECT 1 FROM unnest(aliases) AS a WHERE a ILIKE %s) "
                "ORDER BY kind, code LIMIT %s",
                (like, like, like, like, limit),
            )
            return [area_from_row(row) for row in cur.fetchall()]

    def get_by_code(self, code: str) -> Area | None:
        """One area by its slug, or None."""
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {AREA_SELECT} FROM areas WHERE code = %s", (code,)
            )
            row = cur.fetchone()
        return area_from_row(row) if row else None

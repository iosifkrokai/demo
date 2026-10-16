"""The area repository: the `areas` table, read for territory name resolution."""

from __future__ import annotations

import logging
import re

from db.models.area import Area
from db.store.base import PostgresRepository
from db.store.columns import AREA_SELECT
from db.store.mappers import area_from_row

log = logging.getLogger(__name__)


class PostgresAreaRepository(PostgresRepository):
    """The territories the system can resolve a word to."""

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
            cur.execute(f"SELECT {AREA_SELECT} FROM areas WHERE code = %s", (code,))
            row = cur.fetchone()
        return area_from_row(row) if row else None

    def list_all(self) -> list[Area]:
        """Every territory, districts first. Twenty rows — read and fold in Python."""
        with self._cursor() as cur:
            cur.execute(f"SELECT {AREA_SELECT} FROM areas ORDER BY kind, code")
            return [area_from_row(row) for row in cur.fetchall()]

    def resolve(self, term: str | None) -> str | None:
        """Exact match of a term (code, either name, or alias) to a slug, or None."""
        needle = _normalise(term)
        if not needle:
            return None
        for area in self.list_all():
            terms = (area.code, area.name_ru, area.name_en, *area.aliases)
            if any(_normalise(t) == needle for t in terms if t):
                return area.code
        return None

    def has_boundary(self, code: str) -> bool:
        """Whether this area carries a polygon a search can be scoped to."""
        with self._cursor() as cur:
            cur.execute("SELECT geom IS NOT NULL AS bounded FROM areas WHERE code = %s", (code,))
            row = cur.fetchone()
        return bool(row and row["bounded"])


def _normalise(term: str | None) -> str:
    """Lowercase, drop punctuation, collapse whitespace — for exact matching."""
    squashed = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (term or "").lower()))
    return squashed.strip()

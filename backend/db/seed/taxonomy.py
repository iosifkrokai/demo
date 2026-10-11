"""Reading `data/taxonomy.csv` — the seed's view of the taxonomy.

The file is the source; the `categories` table is the projection. The seed reads
the file to validate a dataset row *while it writes it*, so it needs the parser
even though the service reads the table.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from core.paths import TAXONOMY_CSV
from db.models.category import Category

_LIST_SEP = "|"

_COLUMNS = (
    "code",
    "ru",
    "en",
    "role",
    "osm_tags",
    "visit_minutes",
    "aliases_ru",
    "aliases_en",
)


def _split(value: str) -> tuple[str, ...]:
    """Split a '|'-separated CSV field into a tuple, dropping empties."""
    parts = [p.strip() for p in (value or "").split(_LIST_SEP)]
    return tuple(p for p in parts if p)


def read_categories(path: Path | None = None) -> tuple[Category, ...]:
    """Parse the taxonomy file, failing loudly on a malformed or ambiguous row."""
    source = path or TAXONOMY_CSV
    if not source.exists():  # pragma: no cover — packaging error
        raise FileNotFoundError(f"taxonomy data file missing: {source}")

    with source.open(encoding="utf-8", newline="") as fh:
        rows = [row for row in csv.reader(fh) if row and not row[0].lstrip().startswith("#")]
    if not rows:  # pragma: no cover
        raise ValueError(f"taxonomy data file has no rows: {source}")

    header = tuple(cell.strip() for cell in rows[0])
    if header != _COLUMNS:
        raise ValueError(f"taxonomy header mismatch: {header} != {_COLUMNS}")

    categories: list[Category] = []
    for row in rows[1:]:
        if len(row) != len(_COLUMNS):
            raise ValueError(
                f"taxonomy row has {len(row)} columns, expected {len(_COLUMNS)}: {row}"
            )
        code, ru, en, role, osm_tags, visit_minutes, aliases_ru, aliases_en = row
        if role not in ("sight", "service"):
            raise ValueError(f"taxonomy role must be sight|service, got {role!r} for {code!r}")
        categories.append(
            Category(
                code=code.strip(),
                ru=ru.strip(),
                en=en.strip(),
                role=role,
                osm_tags=_split(osm_tags),
                visit_minutes=int(visit_minutes),
                aliases_ru=_split(aliases_ru),
                aliases_en=_split(aliases_en),
            )
        )

    seen: set[str] = set()
    for cat in categories:
        if cat.code in seen:
            raise ValueError(f"duplicate taxonomy code: {cat.code!r}")
        seen.add(cat.code)
    return tuple(categories)


@lru_cache(maxsize=1)
def all_categories() -> tuple[Category, ...]:
    """The taxonomy, parsed once per process."""
    return read_categories()


@lru_cache(maxsize=1)
def _by_code() -> dict[str, Category]:
    """The taxonomy keyed by canonical code."""
    return {cat.code: cat for cat in all_categories()}


def visit_minutes(code: str) -> int:
    """Default visit time for a canonical code. Raises KeyError if unknown."""
    return _by_code()[code].visit_minutes

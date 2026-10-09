"""Canonical category taxonomy — the single source of category codes.

Every code used by import, retrieval, costing and the API is defined once in
``backend/data/taxonomy.csv`` and read here. There is no second list: modules
import from this file instead of hard-coding their own categories.

Public surface (frozen — see docs/specs/002-grodno-guide-rebuild/plan.md §2):

    Category                 — one canonical category row
    all_categories()         — every category, in file order
    all_codes()              — canonical codes, in file order
    get(code)                — Category | None
    role(code)               — 'sight' | 'service'
    visit_minutes(code)      — default visit time in minutes
    db_values(codes)         — query codes → places.category values (dedup)
    resolve_code(term, ...)  — free text (RU/EN, any inflection) → code | None

The CSV is versioned data (constitution §3): edit the file, not this module.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

Role = Literal["sight", "service"]
Locale = Literal["ru", "en"]

# data/taxonomy.csv sits next to the agent package, under backend/.
_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "taxonomy.csv"

# Columns split their multi-value fields on this separator.
_LIST_SEP = "|"

# Expected header, kept only to fail loudly if the file drifts.
_COLUMNS = (
    "code", "ru", "en", "role", "osm_tags", "visit_minutes",
    "aliases_ru", "aliases_en",
)


@dataclass(frozen=True)
class Category:
    """One canonical category row (immutable — the file is the source)."""

    code: str
    ru: str
    en: str
    role: Role
    osm_tags: tuple[str, ...]
    visit_minutes: int
    aliases_ru: tuple[str, ...] = ()
    aliases_en: tuple[str, ...] = ()


def _split(value: str) -> tuple[str, ...]:
    """Split a '|'-separated CSV field into a tuple, dropping empties."""
    parts = [p.strip() for p in (value or "").split(_LIST_SEP)]
    return tuple(p for p in parts if p)


def _norm(text: str) -> str:
    """Lower-case and fold ё→е so «костёл»/«костел» are one surface form."""
    return text.strip().lower().replace("ё", "е")


@lru_cache(maxsize=1)
def all_categories() -> tuple[Category, ...]:
    """Load and cache the taxonomy for the process lifetime."""
    categories: list[Category] = []
    if not _DATA_FILE.exists():  # pragma: no cover — packaging error
        raise FileNotFoundError(f"taxonomy data file missing: {_DATA_FILE}")

    with _DATA_FILE.open(encoding="utf-8", newline="") as fh:
        rows = [row for row in csv.reader(fh) if row and not row[0].lstrip().startswith("#")]
    if not rows:  # pragma: no cover
        raise ValueError(f"taxonomy data file has no rows: {_DATA_FILE}")

    header = tuple(cell.strip() for cell in rows[0])
    if header != _COLUMNS:
        raise ValueError(f"taxonomy header mismatch: {header} != {_COLUMNS}")

    for row in rows[1:]:
        if len(row) != len(_COLUMNS):
            raise ValueError(f"taxonomy row has {len(row)} columns, expected {len(_COLUMNS)}: {row}")
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


def all_codes() -> tuple[str, ...]:
    """Every canonical code, in file order."""
    return tuple(cat.code for cat in all_categories())


@lru_cache(maxsize=1)
def _code_set() -> frozenset[str]:
    return frozenset(all_codes())


@lru_cache(maxsize=1)
def _surface_index() -> dict[str, str]:
    """normalised surface form → code, for every alias/name/locale.

    Built once and validated: a form that maps to two different codes is
    ambiguous, so it raises instead of silently picking one.
    """
    index: dict[str, str] = {}
    for cat in all_categories():
        forms = {cat.code, cat.ru, cat.en, *cat.aliases_ru, *cat.aliases_en}
        for form in forms:
            key = _norm(form)
            if not key:
                continue
            other = index.get(key)
            if other is not None and other != cat.code:
                raise ValueError(f"surface form {form!r} maps to both {other!r} and {cat.code!r}")
            index[key] = cat.code
    return index


# Russian noun endings stripped when folding an inflected form to its lemma.
# Longest first so «замками» → «замок», not «замка».
_RU_ENDINGS = (
    "ами", "ями", "ах", "ях", "ов", "ев", "ей", "ам", "ям",
    "ой", "ом", "ем", "ы", "и", "а", "я", "у", "ю", "е", "ь",
)
# English plural endings.
_EN_ENDINGS = ("es", "s")


def _fold_candidates(norm: str) -> list[str]:
    """Candidate lemmas for an inflected surface form (may be empty).

    Each stripped stem is also tried with a restored soft consonant («музеями»
    → «музе» → «музей»), because Russian -й/-ь nouns drop it in oblique cases.
    """
    out: list[str] = []
    for ending in _RU_ENDINGS + _EN_ENDINGS:
        if norm.endswith(ending) and len(norm) - len(ending) >= 3:
            stem = norm[: -len(ending)]
            out.extend((stem, stem + "й", stem + "ь"))
    return out


def resolve_code(term: str, locale: Locale = "ru") -> str | None:
    """Resolve free text to a canonical code, or None when unknown.

    Order, cheapest first (locale only disambiguates nothing today — every
    surface form of both locales is indexed; it is accepted for API stability):
      1. exact match on a code, name or alias;
      2. case-insensitive / ё-normalised match;
      3. plural + substring fold (no pg_trgm, no DB round-trip).

    «туалеты» → «туалет», «cafe» → «кафе», «coffee» → «кафе».
    """
    if term is None:
        return None
    raw = term.strip()
    if not raw:
        return None

    # 1. Exact, case-sensitive.
    for cat in all_categories():
        if raw in (cat.code, cat.ru, cat.en) or raw in cat.aliases_ru or raw in cat.aliases_en:
            return cat.code

    # 2. Case-insensitive / ё-normalised.
    norm = _norm(raw)
    index = _surface_index()
    if norm in index:
        return index[norm]

    # 3. Plural fold, then a substring fold as the last resort.
    for candidate in _fold_candidates(norm):
        if candidate in index:
            return index[candidate]

    # A known form may occur INSIDE the query («прогулка по костёлам» → костёл).
    # The reverse containment is deliberately gone: it let a short query inherit
    # a longer code's meaning, so the bare tourist word «остановка» — and even
    # «транспорт» — resolved to the multiword code «остановка транспорта», and a
    # church walk went hunting for bus stops. A word the index does not know
    # returns None, and the caller reports it instead of guessing.
    longest: str | None = None
    for form in index:
        if len(form) >= 4 and form in norm:
            if longest is None or len(form) > len(longest):
                longest = form
    return index[longest] if longest is not None else None


def get(code: str) -> Category | None:
    """Return the Category for a code (or resolvable term), else None."""
    resolved = code if code in _code_set() else resolve_code(code or "")
    if resolved is None:
        return None
    for cat in all_categories():
        if cat.code == resolved:
            return cat
    return None  # pragma: no cover — resolve_code only returns known codes


def role(code: str) -> Role:
    """Role of a code: 'sight' or 'service'. Raises KeyError if unknown."""
    cat = get(code)
    if cat is None:
        raise KeyError(f"unknown category code: {code!r}")
    return cat.role


def visit_minutes(code: str) -> int:
    """Default visit time for a code. Raises KeyError if unknown."""
    cat = get(code)
    if cat is None:
        raise KeyError(f"unknown category code: {code!r}")
    return cat.visit_minutes


def db_values(codes: Iterable[str]) -> list[str]:
    """Map query codes/terms to ``places.category`` values.

    Deduplicates, preserves input order and drops unknown codes. Unknown input
    is dropped rather than passed through, so a junk model category can never
    reach the SQL category filter.
    """
    out: list[str] = []
    for raw in codes or ():
        code = raw if raw in _code_set() else resolve_code(raw or "")
        if code is None:
            continue
        cat = get(code)
        if cat is None:  # pragma: no cover
            continue
        if cat.ru not in out:
            out.append(cat.ru)
    return out

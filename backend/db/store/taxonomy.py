"""The category repository: the `categories` table, read whole."""

from __future__ import annotations

from collections.abc import Iterable

from db.models.category import Category, Role
from db.store.base import ConnectionFactory, PostgresRepository
from db.store.columns import CATEGORY_SELECT
from db.store.mappers import category_from_row

_RU_ENDINGS = (
    "ами",
    "ями",
    "ах",
    "ях",
    "ов",
    "ев",
    "ей",
    "ам",
    "ям",
    "ой",
    "ом",
    "ем",
    "ы",
    "и",
    "а",
    "я",
    "у",
    "ю",
    "е",
    "ь",
)
_EN_ENDINGS = ("es", "s")


_MIN_STEM = 3
_MIN_LOOSE_FORM = 4


def _norm(text: str) -> str:
    """Lower-case and fold ё→е so «костёл»/«костел» are one surface form."""
    return text.strip().lower().replace("ё", "е")


def _fold_candidates(norm: str) -> list[str]:
    """Candidate lemmas for an inflected surface form (may be empty)."""
    out: list[str] = []
    for ending in _RU_ENDINGS + _EN_ENDINGS:
        if norm.endswith(ending) and len(norm) - len(ending) >= _MIN_STEM:
            stem = norm[: -len(ending)]
            out.extend((stem, stem + "й", stem + "ь"))
    return out


class PostgresCategoryRepository(PostgresRepository):
    """The canonical taxonomy, and the folding that turns a word into a code."""

    def __init__(self, connect: ConnectionFactory | None = None) -> None:
        super().__init__(connect)
        self._cache: tuple[Category, ...] | None = None
        self._index: dict[str, str] | None = None

    def all(self) -> tuple[Category, ...]:
        """Every category, in code order. Read once, then held."""
        if self._cache is None:
            with self._cursor() as cur:
                cur.execute(f"SELECT {CATEGORY_SELECT} FROM categories ORDER BY code")
                self._cache = tuple(category_from_row(row) for row in cur.fetchall())
        return self._cache

    def refresh(self) -> None:
        """Drop the held copy — for a process that re-seeds underneath itself."""
        self._cache = None
        self._index = None

    def all_codes(self) -> tuple[str, ...]:
        return tuple(cat.code for cat in self.all())

    def service_codes(self) -> tuple[str, ...]:
        return tuple(cat.code for cat in self.all() if cat.role == "service")

    def get(self, code: str) -> Category | None:
        """The category behind a code or a resolvable term, else None."""
        resolved = code if code in self._code_set() else self.resolve_code(code or "")
        if resolved is None:
            return None
        for cat in self.all():
            if cat.code == resolved:
                return cat
        return None

    def role(self, code: str) -> Role:
        """Role of a code: 'sight' or 'service'. Raises KeyError if unknown."""
        cat = self.get(code)
        if cat is None:
            raise KeyError(f"unknown category code: {code!r}")
        return cat.role

    def visit_minutes(self, code: str) -> int:
        """Default visit time for a code. Raises KeyError if unknown."""
        cat = self.get(code)
        if cat is None:
            raise KeyError(f"unknown category code: {code!r}")
        return cat.visit_minutes

    def db_values(self, codes: Iterable[str]) -> list[str]:
        """Map query codes/terms to ``places.category`` values."""
        out: list[str] = []
        for raw in codes or ():
            resolved = raw if raw in self._code_set() else self.resolve_code(raw or "")
            if resolved is None:
                continue
            cat = self.get(resolved)
            if cat is None:
                continue
            if cat.ru not in out:
                out.append(cat.ru)
        return out

    def resolve_code(self, term: str | None) -> str | None:
        """Resolve free text to a canonical code, or None when unknown."""
        if term is None:
            return None
        raw = term.strip()
        if not raw:
            return None

        for cat in self.all():
            if raw in (cat.code, cat.ru, cat.en) or raw in cat.aliases_ru or raw in cat.aliases_en:
                return cat.code

        norm = _norm(raw)
        index = self._surface_index()
        if norm in index:
            return index[norm]

        for candidate in _fold_candidates(norm):
            if candidate in index:
                return index[candidate]

        longest: str | None = None
        for form in index:
            if (
                len(form) >= _MIN_LOOSE_FORM
                and form in norm
                and (longest is None or len(form) > len(longest))
            ):
                longest = form
        return index[longest] if longest is not None else None

    def _code_set(self) -> frozenset[str]:
        return frozenset(self.all_codes())

    def _surface_index(self) -> dict[str, str]:
        """normalised surface form → code, for every alias/name/locale."""
        if self._index is None:
            index: dict[str, str] = {}
            for cat in self.all():
                forms = {cat.code, cat.ru, cat.en, *cat.aliases_ru, *cat.aliases_en}
                for form in forms:
                    key = _norm(form)
                    if not key:
                        continue
                    other = index.get(key)
                    if other is not None and other != cat.code:
                        raise ValueError(
                            f"surface form {form!r} maps to both {other!r} and {cat.code!r}"
                        )
                    index[key] = cat.code
            self._index = index
        return self._index

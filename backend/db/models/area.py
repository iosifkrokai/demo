"""The `areas` row: a named territory the system can resolve a word to."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Area:
    """One territory. `code` is the slug `find_areas` resolves to and carries."""

    id: int | None = None
    code: str = ""
    name_ru: str = ""
    name_en: str | None = None
    aliases: tuple[str, ...] = ()
    kind: str = "district"
    source: str | None = None
    license: str | None = None
    created_at: datetime | None = None

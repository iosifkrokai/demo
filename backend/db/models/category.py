"""The canonical category — one row of the taxonomy file.

A model only: how the row is shaped. Reading `data/taxonomy.csv` and resolving
free text to a code live in `reference.taxonomy`, next to the data they read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Role = Literal["sight", "service"]
Locale = Literal["ru", "en"]


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

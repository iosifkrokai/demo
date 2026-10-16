"""The canonical category — one row of the taxonomy file."""

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
    highlight: bool = False

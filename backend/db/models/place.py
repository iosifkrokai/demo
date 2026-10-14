"""The `places` row and its two children."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Place:
    """One place: a sight or a service. `category_source` says who owns its category."""

    id: int | None = None
    name: str = ""
    description: str | None = None
    category: str | None = None
    blurb: str | None = None
    fun_fact: str | None = None
    fun_facts: str | None = None
    links: str | None = None
    opening_hours: str | None = None
    ticket_price: str | None = None
    is_free: bool | None = None
    visit_minutes: int | None = None
    district: str | None = None
    town: str | None = None
    lat: float = 0.0
    lon: float = 0.0
    embedding: tuple[float, ...] | None = None
    source_url: str = ""
    category_source: str = "auto"
    photo_url: str | None = None
    photo_author: str | None = None
    photo_license: str | None = None
    photo_source: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class PlaceAlias:
    """A name a place is also known by, in one locale."""

    id: int | None = None
    place_id: int = 0
    alias: str = ""
    locale: str = "ru"
    source: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class PlaceSource:
    """Where a place came from, per provider — keyed on (provider, external_id)."""

    id: int | None = None
    place_id: int = 0
    provider: str = ""
    external_id: str = ""
    url: str | None = None
    license: str | None = None
    fetched_at: datetime | None = None
    verified_at: datetime | None = None
    created_at: datetime | None = None

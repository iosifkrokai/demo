"""The anonymous client, and the preferences it carries.

A client exists beside the accounts: it is what a browser has before anyone signs
in, and what a signing-in account adopts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Client:
    """One anonymous browser identity."""

    id: UUID | None = None
    created_at: datetime | None = None
    last_seen_at: datetime | None = None


@dataclass(frozen=True)
class ClientPreferences:
    """What the panel last left set. Every field is optional — null clears it."""

    client_id: UUID | None = None
    transport: str | None = None
    time_budget_minutes: int | None = None
    party_adults: int | None = None
    party_children: int | None = None
    interests: tuple[str, ...] | None = None
    language: str | None = None
    visit_minutes_by_category: dict[str, int] | None = None
    updated_at: datetime | None = None

"""Accounts and the places they have visited."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class User:
    """One account. `password_hash` is only populated by the login lookup."""

    id: UUID | None = None
    email: str = ""
    password_hash: str | None = None
    display_name: str | None = None
    role: str = "user"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_login_at: datetime | None = None


@dataclass(frozen=True)
class AdminUser(User):
    """A user as the admin list shows one: the row plus its activity count."""

    visited: int = 0


@dataclass(frozen=True)
class VisitedPlace:
    """A place a user marked as visited. The pair is the key."""

    user_id: UUID | None = None
    place_id: int = 0
    visited_at: datetime | None = None

"""Accounts, their sessions, and the places they have visited."""

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
    client_id: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_login_at: datetime | None = None


@dataclass(frozen=True)
class AdminUser(User):
    """A user as the admin list shows one: the row plus its activity counts.

    Not a row of `users` — the counts come from `saved_routes` and
    `visited_places` and exist only for that one listing, which is why this is a
    projection beside the schema-pinned models rather than a change to `User`.
    """

    saved_routes: int = 0
    visited: int = 0


@dataclass(frozen=True)
class UserSession:
    """One signed-in browser. Only the hash of the token is ever stored."""

    token_hash: str = ""
    user_id: UUID | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    last_seen_at: datetime | None = None


@dataclass(frozen=True)
class VisitedPlace:
    """A place a user marked as visited. The pair is the key."""

    user_id: UUID | None = None
    place_id: int = 0
    visited_at: datetime | None = None

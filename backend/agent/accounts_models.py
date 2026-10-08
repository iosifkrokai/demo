"""HTTP models for accounts, visits and the admin surface (spec 005).

Same wire discipline as ``clients_models``: field names and machine reason codes
in, never Russian prose — the UI owns every human sentence. Validation that must
produce a *coded* answer (``invalid_email``, ``weak_password``) is done by the
endpoints with the helpers below, not by pydantic constraints, because a pydantic
422 carries a body the client cannot read as a reason code.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# ── Reason codes — the machine vocabulary the API answers with ────────────────
REASON_STORAGE_UNAVAILABLE = "storage_unavailable"
REASON_NOT_AUTHENTICATED = "not_authenticated"
REASON_NOT_ADMIN = "not_admin"
REASON_INVALID_CREDENTIALS = "invalid_credentials"
REASON_EMAIL_TAKEN = "email_taken"
REASON_WEAK_PASSWORD = "weak_password"
REASON_INVALID_EMAIL = "invalid_email"
REASON_USER_NOT_FOUND = "user_not_found"
REASON_PLACE_NOT_FOUND = "place_not_found"
REASON_SOURCE_TAKEN = "source_taken"
REASON_LAST_ADMIN = "last_admin"
REASON_SELF_ROLE = "self_role"
REASON_SELF_DELETE = "self_delete"
REASON_INVALID_REQUEST = "invalid_request"

#: Cookie the session token travels in; HttpOnly, so no script ever reads it.
SESSION_COOKIE = "grodno_session"

ROLE_USER = "user"
ROLE_ADMIN = "admin"
RoleLiteral = Literal["user", "admin"]

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 200
DISPLAY_NAME_MAX = 120
EMAIL_MAX = 254

# Deliberately permissive: an address is a local part, an «@», a dotted domain,
# no spaces. Anything stricter rejects addresses that work and accepts none that
# do not — the real check is whether the account ever logs in.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(raw: str | None) -> str | None:
    """Lower-case and trim an address; ``None`` when it is not one."""
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value or len(value) > EMAIL_MAX or not _EMAIL_RE.match(value):
        return None
    return value


def password_problem(raw: str | None) -> str | None:
    """The reason code a password fails with, or ``None`` when it is acceptable."""
    if raw is None or len(raw) < PASSWORD_MIN_LENGTH:
        return REASON_WEAK_PASSWORD
    if len(raw) > PASSWORD_MAX_LENGTH:
        return REASON_WEAK_PASSWORD
    return None


# ============================================================================
# Users
# ============================================================================

class RegisterIn(BaseModel):
    """POST /auth/register body."""

    email: str = Field(max_length=EMAIL_MAX)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    display_name: str | None = Field(default=None, max_length=DISPLAY_NAME_MAX)


class LoginIn(BaseModel):
    """POST /auth/login body."""

    email: str = Field(max_length=EMAIL_MAX)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class PublicUser(BaseModel):
    """A user as anyone may see it — ``password_hash`` never leaves the server."""

    id: UUID
    email: str
    display_name: str | None = None
    role: str
    created_at: datetime


class AuthMeOut(BaseModel):
    """GET /auth/me — honest about the anonymous case instead of a bare 401."""

    authenticated: bool
    user: PublicUser | None = None


class AdminUserItem(PublicUser):
    """One row of GET /admin/users, with the counts an admin asks about."""

    client_id: UUID | None = None
    last_login_at: datetime | None = None
    saved_routes: int = 0
    visited: int = 0


class AdminUserListOut(BaseModel):
    items: list[AdminUserItem]
    total: int


class AdminUserPatch(BaseModel):
    """PATCH /admin/users/{id} — partial; only the sent fields change."""

    role: RoleLiteral | None = None
    display_name: str | None = Field(default=None, max_length=DISPLAY_NAME_MAX)


# ============================================================================
# Places (shared shape with the catalogue — see agent/places.place_payload)
# ============================================================================

class PhotoOut(BaseModel):
    url: str
    author: str
    license: str
    source: str | None = None


class LinkOut(BaseModel):
    title: str
    url: str


class PlaceItem(BaseModel):
    """One place, as the catalogue and the admin panel both print it."""

    place_id: int
    source_url: str
    name: str
    category: str | None = None
    town: str | None = None
    district: str | None = None
    lat: float
    lon: float
    visit_minutes: int | None = None
    opening_hours: str | None = None
    blurb: str | None = None
    fun_fact: str | None = None
    fun_facts: list[str] = Field(default_factory=list)
    links: list[LinkOut] = Field(default_factory=list)
    photo: PhotoOut | None = None
    ticket_price: str | None = None


class AdminPlaceListOut(BaseModel):
    items: list[PlaceItem]
    total: int


class AdminPlaceIn(BaseModel):
    """POST /admin/places — a new point needs a name, coordinates and a source."""

    name: str = Field(min_length=1, max_length=300)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    source_url: str = Field(min_length=1, max_length=1000)
    category: str | None = Field(default=None, max_length=120)
    town: str | None = Field(default=None, max_length=200)
    district: str | None = Field(default=None, max_length=200)
    blurb: str | None = None
    fun_fact: str | None = None
    visit_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    opening_hours: str | None = None
    ticket_price: str | None = None


class AdminPlacePatch(BaseModel):
    """PATCH /admin/places/{id} — every field optional, only the sent ones change."""

    name: str | None = Field(default=None, min_length=1, max_length=300)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    category: str | None = Field(default=None, max_length=120)
    town: str | None = Field(default=None, max_length=200)
    district: str | None = Field(default=None, max_length=200)
    blurb: str | None = None
    fun_fact: str | None = None
    visit_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    opening_hours: str | None = None
    ticket_price: str | None = None

    @model_validator(mode="after")
    def _coordinates_are_never_nulled(self) -> AdminPlacePatch:
        """A null `lat`/`lon` would write NULL into a NOT NULL column (a 500).

        The handler updates only the keys present in the body, so *omitting* a
        coordinate is how a client says «leave it where it is». An explicit null
        is a client bug and must fail as a 422, not as a database error.
        """
        for name in ("lat", "lon"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(
                    f"{name} may not be null — omit it to leave it unchanged"
                )
        return self


# ============================================================================
# Visits
# ============================================================================

class VisitedItem(PlaceItem):
    """A visited place: the whole place payload plus *when* it was marked."""

    visited_at: datetime


class VisitedListOut(BaseModel):
    items: list[VisitedItem]
    count: int


class VisitedBulkIn(BaseModel):
    """POST /me/visited — mark a batch (e.g. every stop of a walked route)."""

    place_ids: list[int] = Field(default_factory=list, max_length=500)


class VisitedBulkOut(BaseModel):
    marked: list[int]
    count: int


# ============================================================================
# Dashboard
# ============================================================================

class StatsOut(BaseModel):
    """GET /admin/stats — the four numbers the admin header shows."""

    users: int
    admins: int
    places: int
    visited: int
    saved_routes: int


def public_user(row: dict[str, Any]) -> PublicUser:
    """Project a ``users`` row onto the public shape (drops ``password_hash``)."""
    return PublicUser(
        id=row["id"],
        email=row["email"],
        display_name=row.get("display_name"),
        role=row["role"],
        created_at=row["created_at"],
    )

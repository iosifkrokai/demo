"""HTTP models for the anonymous client entity.

Everything here is machine-readable — field names, reason codes, JSON values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

REASON_STORAGE_UNAVAILABLE = "storage_unavailable"
REASON_ROUTE_NOT_FOUND = "route_not_found"
REASON_INVALID_CLIENT_ID = "invalid_client_id"
REASON_TOO_MANY_ROUTES = "too_many_routes"

CLIENT_ID_HEADER = "X-Client-Id"

TransportLiteral = Literal["pedestrian", "bicycle", "auto"]
LanguageLiteral = Literal["ru", "en"]


class PreferencesIn(BaseModel):
    """PUT /clients/me/preferences body — partial, ``null`` clears a field."""

    transport: TransportLiteral | None = None
    time_budget_minutes: int | None = Field(default=None, ge=0)
    party_adults: int | None = Field(default=None, ge=0, le=50)
    party_children: int | None = Field(default=None, ge=0, le=20)
    interests: list[str] | None = None
    language: LanguageLiteral | None = None
    visit_minutes_by_category: dict[str, int] | None = None

    @field_validator("visit_minutes_by_category")
    @classmethod
    def _no_negative_pace(cls, v: dict[str, int] | None) -> dict[str, int] | None:
        if v is not None:
            for code, minutes in v.items():
                if minutes < 0:
                    raise ValueError(f"visit_minutes_by_category[{code!r}] must be >= 0")
        return v


class PreferencesOut(BaseModel):
    """GET/PUT /clients/me/preferences response — the full stored state."""

    transport: str | None = None
    time_budget_minutes: int | None = None
    party_adults: int | None = None
    party_children: int | None = None
    interests: list[str] | None = None
    language: str | None = None
    visit_minutes_by_category: dict[str, int] | None = None
    updated_at: datetime | None = None


class RouteCreateIn(BaseModel):
    """POST /clients/me/routes body.

    ``plan`` is stored verbatim; the endpoint never inspects or rewrites it.
    """

    query: str = Field(min_length=1, max_length=500)
    plan: dict[str, Any]
    name: str | None = Field(default=None, max_length=200)
    visit_overrides: dict[str, int] | None = None

    @field_validator("query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be empty")
        return v


class RouteCreated(BaseModel):
    """201 response of POST /clients/me/routes."""

    id: UUID
    created_at: datetime


class RouteListItem(BaseModel):
    """One row of GET /clients/me/routes — no heavy geometry.

    ``stop_count``/``distance_m``/``duration_min`` are derived from the stored plan.
    """

    id: UUID
    name: str | None = None
    query: str
    created_at: datetime
    stop_count: int
    distance_m: int | None = None
    duration_min: int | None = None


class RouteDetail(BaseModel):
    """GET /clients/me/routes/{id} — the full saved record."""

    id: UUID
    name: str | None = None
    query: str
    plan: dict[str, Any]
    visit_overrides: dict[str, int] | None = None
    created_at: datetime
    updated_at: datetime


class RoutePatchIn(BaseModel):
    """PATCH /clients/me/routes/{id} body — rename only."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=200)

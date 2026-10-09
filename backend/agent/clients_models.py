"""HTTP models for the anonymous client entity (spec 003).

Everything here is machine-readable: field names, reason codes, JSON values.
No Russian prose is produced or accepted on the wire — the UI owns all
human text (see docs/specs/003-client-entity/spec.md §3).

The one subtlety is the preferences contract: PUT is a *partial* update, so it
must tell "the tourist did not send this field" apart from "the tourist sent
null, which clears it". That is why every field of :class:`PreferencesIn` is
optional with a ``None`` default and the endpoint reads
``model_dump(exclude_unset=True)`` — a field that is absent from the request
simply never appears in the dict, a field sent as ``null`` appears as ``None``
and clears the stored value.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Reason codes — the machine vocabulary of §3. Kept as constants so the API and
# its tests share one spelling.
REASON_STORAGE_UNAVAILABLE = "storage_unavailable"
REASON_ROUTE_NOT_FOUND = "route_not_found"
REASON_INVALID_CLIENT_ID = "invalid_client_id"
REASON_TOO_MANY_ROUTES = "too_many_routes"

CLIENT_ID_HEADER = "X-Client-Id"

TransportLiteral = Literal["pedestrian", "bicycle", "auto"]
LanguageLiteral = Literal["ru", "en"]


# Preferences

class PreferencesIn(BaseModel):
    """PUT /clients/me/preferences body — partial, ``null`` clears a field."""

    transport: TransportLiteral | None = None
    time_budget_minutes: int | None = Field(default=None, ge=0)
    # NULL is a real answer ("не указано"); the server never invents a number.
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
                    raise ValueError(
                        f"visit_minutes_by_category[{code!r}] must be >= 0"
                    )
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


# Saved routes

class RouteCreateIn(BaseModel):
    """POST /clients/me/routes body.

    ``plan`` is the checked TripPlan stored verbatim — the endpoint never
    inspects or rewrites it, so a restored route is exactly what was saved.
    """

    query: str = Field(min_length=1, max_length=500)
    plan: dict[str, Any]
    # As named by the tourist; absent or empty is fine ("untitled").
    name: str | None = Field(default=None, max_length=200)
    # {point id: minutes} — the visit time the tourist changed.
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

    ``stop_count``/``distance_m``/``duration_min`` are derived from the stored
    plan; any of the last two may be null when the plan does not carry them.
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

    # A rename, not a delete: null is not a name, so the field is required.
    name: str = Field(max_length=200)


# Derived list metrics

def route_metrics(
    stop_count: int,
    summary: dict[str, Any] | None,
    budget: dict[str, Any] | None,
) -> dict[str, int | None]:
    """Derive the list columns from the plan's lightweight sub-objects.

    ``stop_count`` comes from the points array; ``distance_m`` from
    ``summary.length_km`` (km → m); ``duration_min`` from
    ``budget.total_minutes`` (visit + walk, what the UI shows) and, failing
    that, from ``summary.time_seconds``. A plan that does not carry a value
    yields null rather than a guessed one.
    """
    distance_m: int | None = None
    duration_min: int | None = None

    km = (summary or {}).get("length_km")
    if isinstance(km, (int, float)) and not isinstance(km, bool):
        distance_m = int(round(float(km) * 1000))

    total = (budget or {}).get("total_minutes")
    if isinstance(total, (int, float)) and not isinstance(total, bool):
        duration_min = int(round(float(total)))
    else:
        secs = (summary or {}).get("time_seconds")
        if isinstance(secs, (int, float)) and not isinstance(secs, bool):
            duration_min = int(round(float(secs) / 60.0))

    return {
        "stop_count": int(stop_count),
        "distance_m": distance_m,
        "duration_min": duration_min,
    }

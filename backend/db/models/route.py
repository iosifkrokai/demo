"""A route a client saved to come back to."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class SavedRoute:
    """One saved route. `plan` is the response body the client was shown.

    Stored whole on purpose: reopening a saved route must show what was actually
    planned, not a re-run that may now answer differently.
    """

    id: UUID | None = None
    client_id: UUID | None = None
    name: str | None = None
    query: str = ""
    plan: dict | None = None
    visit_overrides: dict[str, int] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class SavedRouteSummary:
    """One row of the saved-routes list: the scalars, never the geometry.

    `stop_count`/`distance_m`/`duration_min` are derived from the stored plan by
    the list query, so this projection is not a row of `saved_routes` — fetching
    the whole row would drag a polyline across the wire to print three numbers.
    """

    id: UUID | None = None
    name: str | None = None
    query: str = ""
    created_at: datetime | None = None
    stop_count: int = 0
    distance_m: int | None = None
    duration_min: int | None = None

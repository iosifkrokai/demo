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

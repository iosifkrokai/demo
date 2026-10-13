"""The `threads` rows and the `turns` rows that journal one planning run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Thread:
    """One planning run, with the turns recorded on it."""

    id: str
    created_at: datetime | None = None
    turns: tuple[Turn, ...] = ()


@dataclass(frozen=True)
class Turn:
    """One turn's query, submission and measurement, under a per-thread revision."""

    thread_id: str
    revision: int
    query: str
    submission: dict[str, Any] = field(default_factory=dict)
    measurement: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None

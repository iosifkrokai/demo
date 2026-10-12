"""The `threads` rows and the `turns` rows that journal one planning run.

A thread is one client run session — a first query and the refinements that
follow it — and a turn is one exchange on it: what the tourist asked, what the
agent proposed, and the code's own measurement of that proposal. The measurement
is kept whole so a turn can be replayed without re-planning it.

The shapes mirror `db/models/itinerary.py`: a parent (`Thread`) and its rows
(`Turn`), where the rows carry the parent's id rather than the other way round.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Thread:
    """One planning run, with the turns recorded on it.

    `id` is the client's run session, not an account: a thread exists because a
    browser asked for a route, and the session is what it is addressed by.
    """

    id: str
    created_at: datetime | None = None
    turns: tuple[Turn, ...] = ()


@dataclass(frozen=True)
class Turn:
    """One turn's query, submission and measurement, under a per-thread revision.

    `revision` is assigned by the store against the rows already there, so the
    journal has one gap-free sequence per thread and a turn can be replayed by
    its number.
    """

    thread_id: str
    revision: int
    query: str
    submission: dict[str, Any] = field(default_factory=dict)
    measurement: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None

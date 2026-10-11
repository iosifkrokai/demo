"""How far along a route request is, told by the pipeline itself.

The pipeline reports stage codes, not sentences; clients localise them.
"""

from __future__ import annotations

import contextvars
import time
import uuid
from dataclasses import dataclass, field

STAGE_INTERPRETING = "interpreting_request"
STAGE_SEARCHING = "searching_places"
STAGE_SELECTING = "selecting_candidates"
STAGE_MEASURING_LEGS = "measuring_legs"
STAGE_ORDERING = "ordering_stops"
STAGE_DRAWING = "drawing_line"
STAGE_CHECKING = "checking_requirements"
STAGE_DONE = "done"

ORDER = (
    STAGE_INTERPRETING,
    STAGE_SEARCHING,
    STAGE_SELECTING,
    STAGE_MEASURING_LEGS,
    STAGE_ORDERING,
    STAGE_DRAWING,
    STAGE_CHECKING,
    STAGE_DONE,
)

TTL_S = 300.0

_current: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "route_progress_id", default=None
)


@dataclass
class Tracker:
    """One request's progress: where it got to, and since when."""

    stage: str = STAGE_INTERPRETING
    started_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    done: bool = False
    failed: bool = False


_trackers: dict[str, Tracker] = {}


def new_id() -> str:
    """An id a client may offer up front; only used when it offers none."""
    return uuid.uuid4().hex


def begin(progress_id: str | None) -> str | None:
    """Start tracking ``progress_id`` — called by the API, not by the pipeline.

    Returns the id in use, or ``None`` when the client asked for no progress.
    """
    if not progress_id:
        _current.set(None)
        return None
    _prune()
    _trackers[progress_id] = Tracker()
    _current.set(progress_id)
    return progress_id


def note(stage: str) -> None:
    """Record that the pipeline reached ``stage`` in the current request."""
    progress_id = _current.get()
    if not progress_id:
        return
    tracker = _trackers.get(progress_id)
    if tracker is None:
        return
    tracker.stage = stage
    tracker.updated_at = time.monotonic()


def finish(failed: bool = False) -> None:
    """The request is over: the stages stop being interesting."""
    progress_id = _current.get()
    if not progress_id:
        return
    tracker = _trackers.get(progress_id)
    if tracker is not None:
        tracker.done = True
        tracker.failed = failed
        tracker.updated_at = time.monotonic()
    _current.set(None)


def snapshot(progress_id: str) -> dict[str, object] | None:
    """What the client may be told, or ``None`` when the id is unknown."""
    tracker = _trackers.get(progress_id)
    if tracker is None:
        return None
    return {
        "stage": tracker.stage,
        "done": tracker.done,
        "failed": tracker.failed,
        "elapsed_ms": int((time.monotonic() - tracker.started_at) * 1000),
    }


def _prune() -> None:
    now = time.monotonic()
    for key, tracker in list(_trackers.items()):
        if now - tracker.updated_at > TTL_S:
            _trackers.pop(key, None)

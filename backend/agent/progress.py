"""How far along a route request is, told by the pipeline itself.

The panel used to have two sentences to work with — «отправил запрос» and «рисую
маршрут» — because those were the only things the client could observe from
outside. For a request that takes half a minute that is a long silence, and the
tempting fix (rotate invented captions) would be a lie about what the machine is
doing. So the pipeline reports its own stages instead.

Rules kept here:

* **Codes, not sentences.** The API says ``searching_places``; the client
  localises it. Same rule as reason codes — the backend does not speak one
  language.
* **No infrastructure.** One uvicorn worker in a demo, so a process-local
  dict is enough. If a tracker is gone (restart, TTL), the client is told
  ``unknown_progress_id`` and falls back to what it can observe itself — never to
  an invented stage.
* **A stage is a fact about the past.** ``note()`` records that the pipeline
  *reached* a stage; it never predicts what comes next.
"""

from __future__ import annotations

import contextvars
import time
import uuid
from dataclasses import dataclass, field

#: Stages, in the order they happen. The client owns their wording.
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

#: A tracker is dropped this long after its last update.
TTL_S = 300.0

#: Bound to the request's own id, in the thread that runs the pipeline.
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

    Returns the id in use, or ``None`` when the client asked for no progress:
    then every ``note()`` below is a no-op and costs nothing (the golden harness
    and the benchmarks send no id and must not change behaviour).
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
    if tracker is None:  # TTL ran out under a long request — nothing to record
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

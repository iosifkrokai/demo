"""Where a run has got to, for the panel watching it.
In-process and best-effort: an id nobody is tracking is a miss, not an error.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

DEFAULT_TTL_S = 5 * 60

INTERPRETING_REQUEST = "interpreting_request"
REPAIRING_ROUTE = "repairing_route"
DRAWING_LINE = "drawing_line"
DONE = "done"


@dataclass
class _Entry:
    stage: str
    started_at: float
    touched_at: float
    done: bool = False
    failed: bool = False


class Runs:
    """What each tracked run last reported, and whether it finished."""

    def __init__(self, ttl_s: int = DEFAULT_TTL_S) -> None:
        self.ttl_s = ttl_s
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def begin(self, progress_id: str | None, stage: str = INTERPRETING_REQUEST) -> None:
        """Start tracking a run; an untracked id is what the panel gets otherwise."""
        if not progress_id:
            return
        now = time.monotonic()
        with self._lock:
            self._forget(now)
            self._entries[progress_id] = _Entry(stage=stage, started_at=now, touched_at=now)

    def mark(self, progress_id: str | None, stage: str) -> None:
        """Move a tracked run on. A run nobody began is left untracked."""
        if not progress_id:
            return
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(progress_id)
            if entry is not None and not entry.done:
                entry.stage = stage
                entry.touched_at = now

    def finish(self, progress_id: str | None, *, failed: bool = False) -> None:
        """The run is over. The entry stays readable until it goes stale."""
        if not progress_id:
            return
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(progress_id)
            if entry is not None:
                entry.stage = DONE
                entry.done = True
                entry.failed = failed
                entry.touched_at = now

    def read(self, progress_id: str) -> dict[str, Any] | None:
        """The run's stage and whether it is over, or ``None`` for an unknown id."""
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(progress_id)
            if entry is None or now - entry.touched_at > self.ttl_s:
                self._entries.pop(progress_id, None)
                return None
            return {
                "stage": entry.stage,
                "done": entry.done,
                "failed": entry.failed,
                "elapsed_ms": int((now - entry.started_at) * 1000),
            }

    def _forget(self, now: float) -> None:
        stale = [key for key, e in self._entries.items() if now - e.touched_at > self.ttl_s]
        for key in stale:
            del self._entries[key]


RUNS = Runs()

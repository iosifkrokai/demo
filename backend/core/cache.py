"""The same question, asked twice, computed once.

An in-process TTL/LRU cache for the deterministic expensive thing this process
does: embedding text. `CACHE_BUST=1` turns it off.

A *plan* is not cached, and never will be: the run is the model's, and the same
query twice is two different walks. What is worth caching is the layer under it,
where the same input really does mean the same answer.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


def _flag(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def bust() -> bool:
    """Is the cache switched off for this process?"""
    return _flag("CACHE_BUST").lower() in {"1", "true", "yes", "on"}


DEFAULT_MAXSIZE = 128
DEFAULT_TTL_S = 30 * 60


@dataclass
class _Entry:
    value: Any
    stored_at: float


class TtlLru:
    """A bounded, time-limited store, safe to touch from several requests."""

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE, ttl_s: int = DEFAULT_TTL_S):
        self.maxsize = max(1, maxsize)
        self.ttl_s = max(1, ttl_s)
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.expired = 0

    def get(self, key: str) -> Any | None:
        """The stored value, or None. Expired entries are dropped on the way."""
        if bust():
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            if now - entry.stored_at > self.ttl_s:
                del self._entries[key]
                self.expired += 1
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return entry.value

    def put(self, key: str, value: Any) -> None:
        if bust():
            return
        with self._lock:
            self._entries[key] = _Entry(value=value, stored_at=time.monotonic())
            self._entries.move_to_end(key)
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)
                self.evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            size = len(self._entries)
        total = self.hits + self.misses
        return {
            "enabled": not bust(),
            "hits": self.hits,
            "misses": self.misses,
            "size": size,
            "maxsize": self.maxsize,
            "ttl_s": self.ttl_s,
            "hit_rate": round(self.hits / total, 3) if total else None,
            "evictions": self.evictions,
            "expired": self.expired,
        }


EMBED_CACHE = TtlLru(maxsize=512, ttl_s=DEFAULT_TTL_S)

# Deterministic routing measurements (a cost matrix and the walk it yields) for a
# given set of stops. Valhalla is deterministic, so the same stops with the same
# profile and budget really do mean the same walk, and this is what makes a
# refinement cheap: a stop the model keeps is not re-measured. The key carries the
# *data version* the seed stamped (`db.seed.version` into `data_versions`, read
# through `db/store/datasets.py`): the stops, profile and budget describe the
# request, but a re-seed can move a place underneath it, and the version is what
# makes the old number miss instead of replay. The TTL is now a backstop — it
# bounds how long an entry may live when the version cannot be read — not a
# substitute for the version. Two entries can also differ by the deadline the run
# was measured under (a tight budget skips the final re-order).
MEASURE_CACHE = TtlLru(maxsize=256, ttl_s=DEFAULT_TTL_S)


def embed_key(texts: list[str], model: str) -> str:
    """Identity of an embedding: the model is part of it, so a swap misses."""
    payload = "\u0000".join([model, *texts])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

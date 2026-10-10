"""The same question, asked twice, read once.

In-process TTL/LRU cache of the model's reading; `CACHE_BUST=1` turns it off.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from agent.models import ReaderBrief


def _flag(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _int_env(name: str, default: int) -> int:
    raw = _flag(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def bust() -> bool:
    """Is the cache switched off for this process?"""
    return _flag("CACHE_BUST").lower() in {"1", "true", "yes", "on"}


DEFAULT_MAXSIZE = 128
DEFAULT_TTL_S = 30 * 60


@dataclass
class _Entry:
    value: Any
    stored_at: float
    prompt_hash: str


class TtlLru:
    """A bounded, time-limited store, safe to touch from several requests.

    Small on purpose: a reading is a few hundred bytes, not a database.
    """

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

    def put(self, key: str, value: Any, prompt_hash: str = "") -> None:
        if bust():
            return
        with self._lock:
            self._entries[key] = _Entry(
                value=value, stored_at=time.monotonic(), prompt_hash=prompt_hash
            )
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


INTERPRET_CACHE = TtlLru(
    maxsize=_int_env("INTERPRET_CACHE_SIZE", DEFAULT_MAXSIZE),
    ttl_s=_int_env("INTERPRET_CACHE_TTL_S", DEFAULT_TTL_S),
)
EMBED_CACHE = TtlLru(maxsize=512, ttl_s=DEFAULT_TTL_S)


def _digest(*parts: object) -> str:
    payload = "\u0000".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def prompt_hash(instructions: str) -> str:
    """Identity of the prompt that produced a reading.

    Editing the instructions changes it, so old entries are never found.
    """
    return _digest("prompt", instructions)


def interpret_key(
    query: str, brief: ReaderBrief, instructions: str, model: str | None = None
) -> str:
    """Everything the model is shown, and nothing else.

    `model` is part of the key: the reading is that model's output, not this one's.
    """
    return _digest(
        "interpret",
        model,
        instructions,
        query,
        brief.locale,
        brief.party_adults,
        brief.party_children,
        brief.party_children_ages,
        brief.mobility,
        brief.time_budget_minutes or 0,
        brief.hard_services,
        brief.interests,
        brief.avoid,
        brief.result_mode,
    )


def embed_key(texts: list[str], model: str) -> str:
    return _digest("embed", model, *texts)


def stats() -> dict[str, Any]:
    """Both layers, for the response's debug block and for the logs."""
    return {"interpret": INTERPRET_CACHE.stats(), "embed": EMBED_CACHE.stats()}

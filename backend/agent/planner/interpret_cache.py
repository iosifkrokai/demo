"""The same question, asked twice, read once.

Reading a request is the expensive part of answering it: the model's pass over
the text is 60–89 % of the wall clock (measured: 15.5 s of a 17.4 s request,
25.5 s of 44.6 s) and it is the part that costs money per call. The same text
arrives again and again, though — the demo query typed twice, and above all the
measurement runs: a golden pass asks ~20 identical questions on every run, so
each rerun pays the full model bill for answers it already has.

Why in this process, and not in Redis
-------------------------------------
One worker serves this app (`uvicorn agent.main:app`, no `--workers`), so a
dictionary in memory gives every bit of the speedup a server would, without a
second service to run, monitor and keep in sync. Redis becomes worth its cost
when one of these becomes true, and not before:

  * a second worker appears — note that what breaks first is not this cache but
    the progress tracker (`agent/progress.py`), which also lives in memory:
    a client polling a different worker gets a 404;
  * readings must survive a restart, or be shared between instances.

What is cached, and what is not
-------------------------------
Only the *reading* of the request into ``TripRequirements``. Never the
verifier's verdicts, never the measured services, never the geometry: those are
answers about the world (the database, Valhalla) and a stale one would be a
lie in the panel. The reading is a function of the text, the visible UI filters
and the prompt itself — all of which go into the key — so two requests collide
only when the model would have been asked exactly the same thing.

Two guards against that being wrong:

  * the prompt text is hashed into the key, so editing the instructions or the
    catalogue that is pasted into them invalidates every entry by itself;
  * entries expire (`INTERPRET_CACHE_TTL_S`, default 30 minutes), because the
    reading also depends on what the agent's tools returned from the database,
    which this key cannot see.

`CACHE_BUST=1` turns the cache off for one process — for demos and for
measurement runs, which must time the model and not a dictionary.
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

    Small on purpose: a reading is a few hundred bytes, and the useful working
    set is "the questions this deployment is asked", not a database.
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

    Editing the instructions — or the catalogue pasted into them — changes this,
    and every entry keyed under the old prompt is then simply never found.
    """
    return _digest("prompt", instructions)


def interpret_key(query: str, req: Any, instructions: str) -> str:
    """Everything the model is shown, and nothing else.

    Anything left out here would let two different questions share one answer;
    anything included that the model never sees (the travel profile, the device
    position) would only make the cache miss.
    """
    return _digest(
        "interpret",
        instructions,
        query,
        getattr(req, "locale", None),
        getattr(req, "party_adults", None),
        getattr(req, "party_children", None),
        tuple(getattr(req, "party_children_ages", None) or ()),
        tuple(getattr(req, "mobility", None) or ()),
        getattr(req, "time_budget_minutes", None) or 0,
        tuple(getattr(req, "hard_services", None) or ()),
        tuple(getattr(req, "interests", None) or ()),
        tuple(getattr(req, "avoid", None) or ()),
        getattr(req, "result_mode", None),
    )


def embed_key(texts: list[str], model: str) -> str:
    return _digest("embed", model, *texts)


def stats() -> dict[str, Any]:
    """Both layers, for the response's debug block and for the logs."""
    return {"interpret": INTERPRET_CACHE.stats(), "embed": EMBED_CACHE.stats()}

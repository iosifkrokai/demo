"""The storage layer's typed failures, in one place.

A caller answers these differently — 503 for a database that is down, 409 for a
constraint the user can act on — so they have to be distinguishable by class.
Three identical `StorageUnavailable` classes used to exist, one per store
module, which meant `except StorageUnavailable` only caught one of them; every
repository raises the classes here now.
"""

from __future__ import annotations


class StorageUnavailable(RuntimeError):
    """Postgres could not be reached. The same request may work later (→ 503)."""


class EmailTaken(RuntimeError):
    """Another account already holds this address (→ 409)."""


class DuplicateSource(RuntimeError):
    """Another place already claims this `source_url` (→ 409)."""


class TooManyRoutes(RuntimeError):
    """The client is at its cap on saved routes (→ 409)."""

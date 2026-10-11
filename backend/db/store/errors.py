"""The storage layer's typed failures, in one place.

A caller answers these differently — 503 for a database that is down, 409 for a
constraint the user can act on — so they have to be distinguishable by class, and
every repository has to raise the same one.
"""

from __future__ import annotations


class StorageUnavailableError(RuntimeError):
    """Postgres could not be reached. The same request may work later (→ 503)."""


class EmailTakenError(RuntimeError):
    """Another account already holds this address (→ 409)."""


class DuplicateSourceError(RuntimeError):
    """Another place already claims this `source_url` (→ 409)."""


class TooManyRoutesError(RuntimeError):
    """The client is at its cap on saved routes (→ 409)."""

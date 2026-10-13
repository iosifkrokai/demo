"""The storage layer's typed failures, in one place."""

from __future__ import annotations


class StorageUnavailableError(RuntimeError):
    """Postgres could not be reached. The same request may work later (→ 503)."""


class EmailTakenError(RuntimeError):
    """Another account already holds this address (→ 409)."""


class DuplicateSourceError(RuntimeError):
    """Another place already claims this `source_url` (→ 409)."""

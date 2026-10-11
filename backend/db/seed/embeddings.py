"""Embedding the rows that have none — the seed's half of the vector store.

Computing a vector is the model's job (`db.embeddings.model`); storing it is the
repository's. Nothing here opens its own transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from db.embeddings.model import DB_BATCH, embed_documents


class PlaceBackfill(Protocol):
    """What the backfill asks of a place repository — the three methods it uses.

    A protocol rather than the repository itself: this module runs on the CPU and
    is imported by the seed, so it must not depend on the store.
    """

    def rows_missing_embedding(self) -> list[tuple[int, str, str | None]]: ...

    def upsert_embedding(self, place_id: int, vector: Sequence[float]) -> None: ...

    def commit(self) -> None: ...


def embed_missing(places: PlaceBackfill, *, batch: int = DB_BATCH) -> int:
    """Embed every row with ``embedding IS NULL``; returns how many were written.

    Computing the vectors is this module's job; storing them is the repository's,
    so the model layer never opens a transaction. Rows are keyed by id, so a
    re-run embeds nothing new.
    """
    pending = places.rows_missing_embedding()
    if not pending:
        return 0

    done = 0
    for i in range(0, len(pending), batch):
        chunk = pending[i : i + batch]
        texts = [f"{name}. {blurb or ''}" for _, name, blurb in chunk]
        vectors = embed_documents(texts)
        for (pid, _, _), vec in zip(chunk, vectors, strict=True):
            places.upsert_embedding(pid, vec)
        places.commit()
        done += len(chunk)
    return done

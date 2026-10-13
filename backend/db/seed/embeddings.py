"""Embedding the rows that have none — the seed's half of the vector store."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from db.embeddings.model import DB_BATCH, embed_documents


class PlaceBackfill(Protocol):
    """What the backfill asks of a place repository — the three methods it uses."""

    def rows_missing_embedding(self) -> list[tuple[int, str, str | None]]: ...

    def upsert_embedding(self, place_id: int, vector: Sequence[float]) -> None: ...

    def commit(self) -> None: ...


def embed_missing(places: PlaceBackfill, *, batch: int = DB_BATCH) -> int:
    """Embed every row with ``embedding IS NULL``; returns how many were written."""
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

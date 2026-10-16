"""Embedding the rows that have none — the seed's half of the vector store."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from db.embeddings.model import DB_BATCH, embed_documents

RECIPE = "embed:name+category+town"


class PlaceBackfill(Protocol):
    """What the backfill asks of a place repository — the three methods it uses."""

    def rows_missing_embedding(
        self,
    ) -> list[tuple[int, str, str | None, str | None]]: ...

    def upsert_embedding(self, place_id: int, vector: Sequence[float]) -> None: ...

    def commit(self) -> None: ...


def embedded_text(name: str, category: str | None, town: str | None) -> str:
    """The text a row's vector is a projection of.

    The category and the town are what a tourist actually says («музеи Гродно»): a vector
    of the name alone leaves 45% of the corpus duplicated and reaches nothing. The blurb
    is deliberately not here — measured, it dilutes the two that matter (precision@10
    0.63 against 0.75), and only 1% of the rows have one.
    """
    parts = [f"{name}."]
    for field in (category, town):
        if field:
            parts.append(f"{field}.")
    return " ".join(parts)


def embed_missing(places: PlaceBackfill, *, batch: int = DB_BATCH) -> int:
    """Embed every row with ``embedding IS NULL``; returns how many were written."""
    pending = places.rows_missing_embedding()
    if not pending:
        return 0

    done = 0
    for i in range(0, len(pending), batch):
        chunk = pending[i : i + batch]
        texts = [embedded_text(name, category, town) for _, name, category, town in chunk]
        vectors = embed_documents(texts)
        for (pid, *_), vec in zip(chunk, vectors, strict=True):
            places.upsert_embedding(pid, vec)
        places.commit()
        done += len(chunk)
    return done

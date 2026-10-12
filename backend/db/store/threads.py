"""The planning journal: the `threads` parent, the `turns` rows, and their reads.

A thread is a client run session; a turn is one query and the code's answer to
it. The store owns the revision sequence — the next number is computed in SQL
against the rows already there — so the journal has one gap-free sequence per
thread and a turn can be replayed by number.

The shape mirrors `db/store/itineraries.py`: a parent read that attaches its
rows, written and read through one connection the repository owns.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from db.models.thread import Thread, Turn
from db.store.base import PostgresRepository
from db.store.mappers import model_from_row

log = logging.getLogger(__name__)

TURN_COLUMNS: tuple[str, ...] = (
    "thread_id",
    "revision",
    "query",
    "submission",
    "measurement",
    "created_at",
)

TURN_SELECT = ", ".join(TURN_COLUMNS)


class ThreadRepository(Protocol):
    """The surface `agent.thread` depends on (a fake implements this)."""

    def append(
        self,
        thread_id: str,
        *,
        query: str,
        submission: dict[str, Any],
        measurement: dict[str, Any],
    ) -> Turn: ...

    def latest(self, thread_id: str) -> Turn | None: ...

    def journal(self, thread_id: str) -> Thread | None: ...

    def delete(self, thread_id: str) -> bool: ...


class PostgresThreadRepository(PostgresRepository):
    """The journal of planning turns, written and read through one connection."""

    def append(
        self,
        thread_id: str,
        *,
        query: str,
        submission: dict[str, Any],
        measurement: dict[str, Any],
    ) -> Turn:
        """Record one turn, minting its revision against the rows already there."""
        with self._cursor() as cur:
            # The parent must exist before the FK allows the row; a thread is
            # created by its first turn and never listed on its own.
            cur.execute(
                "INSERT INTO threads (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
                (thread_id,),
            )
            cur.execute(
                f"""
                INSERT INTO turns (thread_id, revision, query, submission, measurement)
                VALUES (
                    %s,
                    COALESCE((SELECT max(revision) + 1 FROM turns WHERE thread_id = %s), 0),
                    %s, %s, %s
                )
                RETURNING {TURN_SELECT}
                """,
                (thread_id, thread_id, query, Jsonb(submission), Jsonb(measurement)),
            )
            row = cur.fetchone()
        assert row is not None
        return model_from_row(Turn, row)

    def latest(self, thread_id: str) -> Turn | None:
        """The last turn recorded on a thread — the route as it currently stands."""
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {TURN_SELECT} FROM turns WHERE thread_id = %s "
                "ORDER BY revision DESC LIMIT 1",
                (thread_id,),
            )
            row = cur.fetchone()
        return model_from_row(Turn, row) if row is not None else None

    def journal(self, thread_id: str) -> Thread | None:
        """The whole thread — parent and every turn, oldest first — or ``None``."""
        with self._cursor() as cur:
            cur.execute("SELECT id, created_at FROM threads WHERE id = %s", (thread_id,))
            parent = cur.fetchone()
            if parent is None:
                return None
            cur.execute(
                f"SELECT {TURN_SELECT} FROM turns WHERE thread_id = %s ORDER BY revision",
                (thread_id,),
            )
            turns = tuple(model_from_row(Turn, row) for row in cur.fetchall())
        return Thread(id=parent["id"], created_at=parent["created_at"], turns=turns)

    def delete(self, thread_id: str) -> bool:
        """Drop a thread and, by cascade, its turns. ``False`` when it is unknown."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM threads WHERE id = %s", (thread_id,))
            return cur.rowcount > 0

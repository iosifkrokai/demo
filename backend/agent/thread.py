"""The server-side thread: a journal of turns, and what the journal outranks.

The client holds a snapshot of the route it currently sees and re-sends the whole
thing on every refinement. The server keeps its own journal, keyed by the client's
run session, of what each turn asked and what the code answered, so a refinement
reconciles against the journal rather than against the snapshot alone: the route
the journal recorded is the spine the next turn builds on, and a base stop that
route carries survives whatever the snapshot claims. The snapshot is only what the
server falls back to when the journal has nothing — a first turn, or one that
never completed.

Both halves are best-effort. A journal read that fails leaves the snapshot as the
only base, and a journal write that fails is logged: neither is allowed to turn a
plan the tourist already holds into an error.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any

from agent.models import BriefStop, ReaderBrief, RefinementBrief
from db.store.errors import StorageUnavailableError

if TYPE_CHECKING:
    from db.models.thread import Turn
    from db.store.threads import ThreadRepository

log = logging.getLogger(__name__)

__all__ = [
    "journal_stops",
    "previous_stops",
    "reconcile",
    "reconcile_brief",
    "record",
    "thread_key",
]


def thread_key(session_id: str, *, identity: str | None = None) -> str:
    """The journal key for a client run, namespaced by the session that names it.

    A run is addressed by the id the client mints, but it belongs to the session
    that asked for it: with an identity for that session the key carries it, so a
    thread is only ever read back under the same session. Without one — an
    anonymous run — the bare id stands.
    """
    return f"{identity}:{session_id}" if identity else session_id


def journal_stops(turn: Turn | None) -> tuple[BriefStop, ...]:
    """The stops a recorded turn left on the route, in its walking order.

    The measurement is the code's own answer — the order the walk actually
    settled into — so it is a firmer base than a snapshot the client may have
    built from a route the server never produced.
    """
    if turn is None:
        return ()
    stops: list[BriefStop] = []
    for card in turn.measurement.get("results") or ():
        place_id = card.get("id")
        if place_id is None:
            continue
        stops.append(BriefStop(place_id=int(place_id), name=str(card.get("name") or "")))
    return tuple(stops)


def reconcile(context: RefinementBrief | None, recorded: Turn | None) -> RefinementBrief | None:
    """The refinement as the server will run it; the journal is the spine.

    With a recorded turn, the base stops the next plan must preserve are the
    journal's route, with the snapshot's own points merged in for anything the
    journal does not know — the tourist's position, a stop they pinned by hand.
    A stop the tourist has just deleted is dropped from both. Without a record
    there is nothing to reconcile against, so the snapshot stands as sent.
    """
    if context is None or recorded is None:
        return context
    excluded = set(context.excluded_ids)
    base = [stop for stop in journal_stops(recorded) if stop.place_id not in excluded]
    known = {stop.place_id for stop in base}
    for point in context.base_points:
        if point.place_id in known or point.place_id in excluded:
            continue
        base.append(point)
    return RefinementBrief(
        instruction=context.instruction,
        revision=context.revision,
        excluded_ids=context.excluded_ids,
        base_points=tuple(base),
    )


def previous_stops(
    repo: ThreadRepository,
    *,
    thread_id: str | None,
    context: RefinementBrief | None,
) -> tuple[BriefStop, ...]:
    """The route a refinement starts from: the journal's, or the client's snapshot.

    The journal is the server's own record of the route it actually returned, so
    where the thread has a turn it is the authority on what the previous route
    was. The base points the client re-sends are its own claim, and stand only for
    a thread the journal has nothing for. Best-effort, like every other journal
    read: an unreadable one falls back to the snapshot rather than failing the
    turn, and a fresh request (no refinement) has no previous route at all.
    """
    if context is None:
        return ()
    if thread_id:
        try:
            recorded = repo.latest(thread_id)
        except StorageUnavailableError as exc:
            log.warning("thread %s: journal unreadable (%s)", thread_id, exc)
            recorded = None
        if recorded is not None:
            return journal_stops(recorded)
    return context.base_points


def reconcile_brief(repo: ThreadRepository, *, thread_id: str, brief: ReaderBrief) -> ReaderBrief:
    """The brief with its refinement checked against the thread's journal.

    A thread with no record, or a journal the database would not answer, leaves
    the brief exactly as the client sent it.
    """
    if brief.context is None:
        return brief
    try:
        recorded = repo.latest(thread_id)
    except StorageUnavailableError as exc:
        log.warning("thread %s: journal unreadable (%s)", thread_id, exc)
        return brief
    return dataclasses.replace(brief, context=reconcile(brief.context, recorded))


def record(
    repo: ThreadRepository,
    *,
    thread_id: str,
    query: str,
    submission: dict[str, Any],
    measurement: dict[str, Any],
) -> None:
    """Append this turn to the thread's journal; a failed write is only logged."""
    try:
        repo.append(thread_id, query=query, submission=submission, measurement=measurement)
    except Exception as exc:  # a journal write must never fail the tourist's plan
        log.warning("thread %s: turn not recorded (%s: %s)", thread_id, type(exc).__name__, exc)

"""The server-side thread: a journal of turns, and what the journal outranks."""

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
    """The journal key for a client run, namespaced by the session that names it."""
    return f"{identity}:{session_id}" if identity else session_id


def journal_stops(turn: Turn | None) -> tuple[BriefStop, ...]:
    """The stops a recorded turn left on the route, in its walking order."""
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
    """The refinement as the server will run it; the journal is the spine."""
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
    """The route a refinement starts from: the journal's, or the client's snapshot."""
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
    """The brief with its refinement checked against the thread's journal."""
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
    except Exception as exc:
        log.warning("thread %s: turn not recorded (%s: %s)", thread_id, type(exc).__name__, exc)

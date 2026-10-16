"""When a place is open: the hours a plan can be checked against, and the silence.

`opening_hours` is a reference string, not a schedule — the data README says so, and the
sets hold 339 spellings of it: OSM syntax (`Mo-Su 10:00-22:00`, `24/7`), Russian prose
(«ежедневно 9:00–18:00», «вт–вс 10:00–18:00») and sentences that are not a schedule at
all («снаружи — в любое время», «по записи»). This file reads the subset it understands
exactly, and answers ``None`` — unknown — for everything else, because a schedule
misread is a promise nobody made.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

WALK_UTC_OFFSET = timedelta(hours=3)

ALWAYS: frozenset[str] = frozenset({"24/7", "круглосуточно"})

DAYS: dict[str, int] = {
    "mo": 0,
    "tu": 1,
    "we": 2,
    "th": 3,
    "fr": 4,
    "sa": 5,
    "su": 6,
    "пн": 0,
    "вт": 1,
    "ср": 2,
    "чт": 3,
    "пт": 4,
    "сб": 5,
    "вс": 6,
}

EVERY_DAY: frozenset[int] = frozenset(range(7))
WEEK_MINUTES = 7 * 24 * 60

MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24

_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-"})
_TIME = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class Window:
    """A span of the week a place is open: days it starts on, and minutes of a day."""

    days: frozenset[int]
    start: int
    end: int


def _clean(text: str) -> str:
    spaced = re.sub(r"\s*-\s*", "-", text.lower().translate(_DASHES))
    return " ".join(spaced.split())


def _minutes(hours: str, minutes: str) -> int | None:
    h, m = int(hours), int(minutes)
    if m >= MINUTES_PER_HOUR or h > HOURS_PER_DAY or (h == HOURS_PER_DAY and m):
        return None
    return h * MINUTES_PER_HOUR + m


def _days(spec: str) -> frozenset[int] | None:
    """The weekdays a day-spec names, every day when it names none."""
    if not spec or spec == "ежедневно":
        return EVERY_DAY
    days: set[int] = set()
    for token in spec.split(","):
        head, _, tail = token.partition("-")
        first = DAYS.get(head)
        last = DAYS.get(tail) if tail else first
        if first is None or last is None:
            return None
        span = (last - first) % 7
        days.update((first + step) % 7 for step in range(span + 1))
    return frozenset(days)


def windows(text: str | None) -> list[Window] | None:
    """The opening windows a string states, or ``None`` when it states something else."""
    if not text:
        return None
    rules = [rule.strip() for rule in _clean(text).split(";") if rule.strip()]
    if not rules:
        return None
    out: list[Window] = []
    for rule in rules:
        parts = rule.split()
        span = _TIME.match(parts[-1])
        if span is None:
            return None
        start = _minutes(span.group(1), span.group(2))
        end = _minutes(span.group(3), span.group(4))
        days = _days(" ".join(parts[:-1]))
        if start is None or end is None or days is None:
            return None
        out.append(Window(days=days, start=start, end=end))
    return out


def local_at(instant: datetime) -> datetime:
    """The wall clock in Grodno for an instant. Belarus is UTC+3 all year, no DST."""
    return instant + WALK_UTC_OFFSET


def parse_moment(value: datetime | str) -> datetime:
    """One instant from a wire value: a wall clock is Grodno time, an instant is placed.

    The same rule for the request and for whoever compares two moments, so "10:00" means
    ten in the morning in Grodno wherever it is read.
    """
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone(WALK_UTC_OFFSET))
    return moment.astimezone(UTC)


def opens_at(text: str | None, when: datetime) -> bool | None:
    """Whether a place is open at ``when``, or ``None`` when the string does not say.

    ``when`` is the local wall clock — pass ``local_at(now_utc)`` for "now".
    """
    cleaned = _clean(text or "")
    if not cleaned:
        return None
    if cleaned in ALWAYS:
        return True
    spans = windows(text)
    if not spans:
        return None
    minutes = when.weekday() * 60 * 24 + when.hour * 60 + when.minute
    for span in spans:
        for day in span.days:
            start = day * 60 * 24 + span.start
            end = day * 60 * 24 + (span.end if span.end > span.start else span.end + 60 * 24)
            if start <= minutes < end or start <= minutes + WEEK_MINUTES < end:
                return True
    return False

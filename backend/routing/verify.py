"""Step 7b — Independent requirement verifier."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, NamedTuple

from core import constants
from core.hours import local_at, opens_at
from core.prices import free_entry
from routing.models import (
    REASON_MUST_VISIT_OUTSIDE as REASON_MUST_VISIT_OUTSIDE_CODE,
    CategoryFacts,
    Requirement,
    TripRequirements,
)

__all__ = [
    "INFEASIBLE_REASONS",
    "REASON_CODES",
    "REPAIRABLE_REASONS",
    "geometry_ok",
    "overall_status",
    "verify",
    "verify_catalogue",
    "verify_summary",
]


REASON_MUST_VISIT_OK = "must_visit_on_route"
REASON_MUST_VISIT_ABSENT = "must_visit_absent"
REASON_MUST_VISIT_UNROUTABLE = "must_visit_unroutable"
REASON_MUST_VISIT_UNSPECIFIED = "must_visit_unspecified"
REASON_MUST_VISIT_OUTSIDE = REASON_MUST_VISIT_OUTSIDE_CODE

REASON_SERVICE_OK = "service_on_route"
REASON_SERVICE_ALONG_ROUTE = "service_along_route"
REASON_SERVICE_NOT_MEASURED = "service_not_measured"
REASON_HARD_SERVICE_ABSENT = "hard_service_absent"
REASON_SOFT_SERVICE_ABSENT = "soft_service_absent"

REASON_INTEREST_OK = "interest_on_route"
REASON_INTEREST_ABSENT = "interest_absent"

REASON_MUST_VISIT_IN_CATALOGUE = "must_visit_in_catalogue"
REASON_SERVICE_IN_CATALOGUE = "service_in_catalogue"
REASON_INTEREST_IN_CATALOGUE = "interest_in_catalogue"

REASON_AVOID_OK = "avoid_honoured"
REASON_AVOID_VIOLATED = "avoid_violated"

REASON_FREE_OK = "free_entry_proven"
REASON_FREE_PAID = "paid_stop_on_route"
REASON_FREE_UNKNOWN = "admission_unknown"

REASON_OPEN_OK = "open_at_visit"
REASON_OPEN_CLOSED = "closed_at_visit"
REASON_OPEN_UNKNOWN = "hours_unknown"
REASON_OPEN_NOT_DATED = "visit_time_unknown"

REASON_STEP_FREE_PROVEN = "step_free_proven"
REASON_STEPS_ON_ROUTE = "steps_on_route"
REASON_STEPS_UNKNOWN = "steps_unknown"

REASON_CODE_UNSPECIFIED = "category_code_unspecified"
REASON_CODE_UNKNOWN = "category_code_unknown"

REASON_ROUTE_MISSING = "route_missing"
REASON_GEOMETRY_MISSING = "geometry_missing"


class ServiceAlongEvidence(NamedTuple):
    """Three states: nothing supplied keeps the older reading, ``measured=True`` is
    evidence, ``measured=False`` is ``uncertain``, never ``unmet``.
    """

    measured: bool
    by_code: dict[str, list[dict[str, Any]]]


class StepsAlong(NamedTuple):
    """What the catalogue's stairways say about the drawn line.

    ``measured=False`` is the data the code could not consult — a missing line or an
    unreachable catalogue — and it is ``uncertain``, never ``unmet``.
    """

    measured: bool
    steps: list[dict[str, Any]]


def steps_evidence(repos: Any, shape: Any) -> StepsAlong:
    """The stairways the route runs over, or the honest word that they were not read."""
    if repos is None or not shape:
        return StepsAlong(measured=False, steps=[])
    try:
        answer = repos.places.steps_along(shape)
    except Exception:
        return StepsAlong(measured=False, steps=[])
    return StepsAlong(measured=True, steps=list(answer.get("items") or []))


REASON_CODES: frozenset[str] = frozenset(
    {
        REASON_MUST_VISIT_OK,
        REASON_MUST_VISIT_OUTSIDE,
        REASON_MUST_VISIT_ABSENT,
        REASON_MUST_VISIT_UNROUTABLE,
        REASON_MUST_VISIT_UNSPECIFIED,
        REASON_SERVICE_OK,
        REASON_SERVICE_ALONG_ROUTE,
        REASON_SERVICE_NOT_MEASURED,
        REASON_HARD_SERVICE_ABSENT,
        REASON_SOFT_SERVICE_ABSENT,
        REASON_INTEREST_OK,
        REASON_INTEREST_ABSENT,
        REASON_MUST_VISIT_IN_CATALOGUE,
        REASON_SERVICE_IN_CATALOGUE,
        REASON_INTEREST_IN_CATALOGUE,
        REASON_AVOID_OK,
        REASON_AVOID_VIOLATED,
        REASON_FREE_OK,
        REASON_FREE_PAID,
        REASON_FREE_UNKNOWN,
        REASON_OPEN_OK,
        REASON_OPEN_CLOSED,
        REASON_OPEN_UNKNOWN,
        REASON_OPEN_NOT_DATED,
        REASON_STEP_FREE_PROVEN,
        REASON_STEPS_ON_ROUTE,
        REASON_STEPS_UNKNOWN,
        REASON_CODE_UNSPECIFIED,
        REASON_CODE_UNKNOWN,
        REASON_ROUTE_MISSING,
        REASON_GEOMETRY_MISSING,
    }
)

INFEASIBLE_REASONS: frozenset[str] = frozenset(
    {
        REASON_MUST_VISIT_UNROUTABLE,
        REASON_MUST_VISIT_ABSENT,
        REASON_MUST_VISIT_OUTSIDE,
        REASON_HARD_SERVICE_ABSENT,
    }
)

REPAIRABLE_REASONS: frozenset[str] = frozenset(
    {
        REASON_MUST_VISIT_ABSENT,
        REASON_MUST_VISIT_UNSPECIFIED,
        REASON_HARD_SERVICE_ABSENT,
        REASON_INTEREST_ABSENT,
        REASON_AVOID_VIOLATED,
        REASON_CODE_UNSPECIFIED,
        REASON_CODE_UNKNOWN,
        REASON_FREE_PAID,
        REASON_FREE_UNKNOWN,
        REASON_OPEN_CLOSED,
        REASON_OPEN_UNKNOWN,
        REASON_STEPS_ON_ROUTE,
        REASON_STEPS_UNKNOWN,
    }
)

Status = Literal["pending", "satisfied", "unmet", "uncertain"]


def _route_stops(plan: Any) -> list[Any]:
    """The ordered stops of the plan, from whatever shape the caller passed."""
    if plan is None:
        return []
    if isinstance(plan, dict):
        route = plan.get("route")
        return list(route) if route else []
    route = getattr(plan, "route", None)
    if route is not None:
        return list(route)
    if isinstance(plan, (list, tuple)):
        return list(plan)
    return []


def _plan_trace(plan: Any) -> dict[str, Any]:
    if isinstance(plan, dict):
        trace = plan.get("trace")
        return trace if isinstance(trace, dict) else {}
    trace = getattr(plan, "trace", None)
    return trace if isinstance(trace, dict) else {}


def _unroutable_ids(trace: dict[str, Any]) -> set[int]:
    """Stop ids the pruner reported as unroutable in this order."""
    out: set[int] = set()
    for key in ("unroutable_stops", "pruned_stops"):
        for item in trace.get(key) or []:
            if isinstance(item, int):
                pid: Any = item
            elif isinstance(item, dict):
                pid = item.get("id")
            else:
                pid = item.id
            if isinstance(pid, int):
                out.add(pid)
    return out


def geometry_ok(geometry: Any) -> bool:
    """True when ``geometry`` is a usable line: ≥2 coordinates."""
    if geometry is None or not isinstance(geometry, dict):
        return False

    gtype = geometry.get("type")
    if gtype == "Feature":
        return geometry_ok(geometry.get("geometry"))
    if gtype == "FeatureCollection":
        feats = geometry.get("features") or []
        return any(geometry_ok(f.get("geometry")) for f in feats if isinstance(f, dict))
    if gtype == "Point":
        return False

    coords = geometry.get("coordinates")
    return _coord_count(coords) >= constants.MIN_LEG_POINTS


def _coord_count(coords: Any) -> int:
    """Number of positions in a coordinate array (recurses into Multi*)."""
    if not isinstance(coords, (list, tuple)) or not coords:
        return 0
    first = coords[0]
    if isinstance(first, (list, tuple)):
        return max((_coord_count(c) for c in coords), default=0)
    return len(coords)


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def norm_name(name: str | None) -> str:
    text = (name or "").strip().lower().replace("ё", "е")
    text = _PUNCT_RE.sub(" ", text)
    return " ".join(text.split())


def _norm_cat(value: str | None) -> str:
    return (value or "").strip().lower().replace("ё", "е")


def _canonical_code(code: str | None, codes: frozenset[str]) -> str | None:
    """The code itself when it is canonical, else None."""
    if not code:
        return None
    return code if code in codes else None


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read a stop's field whether the plan arrived as objects or as mappings."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _stop_matches_code(stop: Any, code: str, codes: frozenset[str]) -> bool:
    """Does a route stop belong to the canonical category ``code``?"""
    stop_cat = _norm_cat(_field(stop, "category"))
    norm_code = _norm_cat(code)
    if not stop_cat or not norm_code:
        return False
    if stop_cat == norm_code or norm_code in stop_cat or stop_cat in norm_code:
        return True
    resolved = _canonical_code(stop_cat, codes)
    return resolved is not None and _norm_cat(resolved) == norm_code


def _set(requirement: Requirement, status: Status, place_ids: list[int], reason: str) -> None:
    requirement.status = status
    requirement.place_ids = list(place_ids)
    requirement.reason = reason


def _match_named(r: Requirement, stops: list[Any]) -> Any | None:
    """The stop proving a named/numbered must-visit, or None."""
    if r.place_id is not None:
        for stop in stops:
            if _field(stop, "id") == r.place_id:
                return stop
    if r.name:
        wanted = norm_name(r.name)
        for stop in stops:
            if wanted and norm_name(_field(stop, "name")) == wanted:
                return stop
    return None


def _verify_must_visit(
    r: Requirement,
    stops: list[Any],
    route_present: bool,
    unroutable: set[int],
    geom_ok: bool,
) -> None:
    if r.reason == REASON_MUST_VISIT_OUTSIDE:
        _set(r, "unmet", [], REASON_MUST_VISIT_OUTSIDE)
        return

    if r.place_id is None and not r.name:
        _set(r, "uncertain", [], REASON_MUST_VISIT_UNSPECIFIED)
        return

    if r.place_id is not None and r.place_id in unroutable:
        _set(r, "unmet", [], REASON_MUST_VISIT_UNROUTABLE)
        return

    match = _match_named(r, stops)

    if match is not None:
        pid = _field(match, "id")
        ids = [pid] if isinstance(pid, int) else []
        if geom_ok:
            _set(r, "satisfied", ids, REASON_MUST_VISIT_OK)
        else:
            _set(r, "uncertain", ids, REASON_GEOMETRY_MISSING)
        return

    if not route_present:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
        return
    _set(r, "unmet", [], REASON_MUST_VISIT_ABSENT)


def _evidence_for(
    r: Requirement, services_along: ServiceAlongEvidence | None, codes: frozenset[str]
) -> tuple[bool | None, list[dict[str, Any]] | None]:
    """``(measured_ok, items)`` for this requirement."""
    if services_along is None:
        return None, None
    if not services_along.measured:
        return False, None
    if not r.code:
        return True, None
    code = _canonical_code(r.code, codes)
    if code is None:
        return True, None
    return True, services_along.by_code.get(code, [])


def _verify_service(
    r: Requirement,
    stops: list[Any],
    route_present: bool,
    geom_ok: bool,
    codes: frozenset[str],
    *,
    evidence: tuple[bool | None, list[dict[str, Any]] | None] = (None, None),
) -> None:
    measured_ok, measured = evidence
    if not r.code:
        _set(r, "uncertain", [], REASON_CODE_UNSPECIFIED)
        return
    code = _canonical_code(r.code, codes)
    if code is None:
        _set(r, "uncertain", [], REASON_CODE_UNKNOWN)
        return

    matched = [s for s in stops if _stop_matches_code(s, code, codes)]
    if matched:
        ids = [i for i in (_field(s, "id") for s in matched) if isinstance(i, int)]
        if geom_ok:
            _set(r, "satisfied", ids, REASON_SERVICE_OK)
        else:
            _set(r, "uncertain", ids, REASON_GEOMETRY_MISSING)
        return

    if measured_ok is False and r.strength == "hard":
        _set(r, "uncertain", [], REASON_SERVICE_NOT_MEASURED)
        return
    if measured:
        ids = [p["id"] for p in measured if isinstance(p.get("id"), int)]
        if geom_ok:
            _set(r, "satisfied", ids, REASON_SERVICE_ALONG_ROUTE)
        else:
            _set(r, "uncertain", ids, REASON_GEOMETRY_MISSING)
        return

    if not route_present:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
        return
    reason = REASON_HARD_SERVICE_ABSENT if r.strength == "hard" else REASON_SOFT_SERVICE_ABSENT
    _set(r, "unmet", [], reason)


def _verify_interest(
    r: Requirement,
    stops: list[Any],
    route_present: bool,
    geom_ok: bool,
    codes: frozenset[str],
) -> None:
    if not r.code:
        _set(r, "uncertain", [], REASON_CODE_UNSPECIFIED)
        return
    code = _canonical_code(r.code, codes)
    if code is None:
        _set(r, "uncertain", [], REASON_CODE_UNKNOWN)
        return

    matched = [s for s in stops if _stop_matches_code(s, code, codes)]
    if matched:
        ids = [s.id for s in matched if isinstance(getattr(s, "id", None), int)]
        if geom_ok:
            _set(r, "satisfied", ids, REASON_INTEREST_OK)
        else:
            _set(r, "uncertain", ids, REASON_GEOMETRY_MISSING)
        return

    if not route_present:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
        return
    _set(r, "unmet", [], REASON_INTEREST_ABSENT)


def _verify_avoid(
    r: Requirement, stops: list[Any], route_present: bool, codes: frozenset[str]
) -> None:
    if not r.code:
        _set(r, "uncertain", [], REASON_CODE_UNSPECIFIED)
        return
    code = _canonical_code(r.code, codes)
    if code is None:
        _set(r, "uncertain", [], REASON_CODE_UNKNOWN)
        return

    offenders = [s for s in stops if _stop_matches_code(s, code, codes)]
    if offenders:
        ids = [i for i in (_field(s, "id") for s in offenders) if isinstance(i, int)]
        _set(r, "unmet", ids, REASON_AVOID_VIOLATED)
        return
    if not route_present:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
        return
    _set(r, "satisfied", [], REASON_AVOID_OK)


def _ids_of(rows: list[Any]) -> list[int]:
    return [i for i in (_field(row, "id") for row in rows) if isinstance(i, int)]


def _by_admission(rows: list[Any]) -> tuple[list[Any], list[Any], list[Any]]:
    """The rows as (free, paid, price-unknown). A price the data does not state is
    not a free one, and it is not a paid one either.
    """
    groups: tuple[list[Any], list[Any], list[Any]] = ([], [], [])
    for row in rows:
        groups[{True: 0, False: 1, None: 2}[free_entry(_field(row, "ticket_price"))]].append(row)
    return groups


def _verify_free(r: Requirement, stops: list[Any], route_present: bool, geom_ok: bool) -> None:
    """Whether the walk costs nothing to enter. Every stop has to be proven free: one
    paid stop is the answer `unmet`, and a price nobody wrote down is `uncertain`.
    """
    if not route_present:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
        return
    free, paid, unknown = _by_admission(stops)
    if paid:
        _set(r, "unmet", _ids_of(paid), REASON_FREE_PAID)
        return
    if unknown:
        _set(r, "uncertain", _ids_of(unknown), REASON_FREE_UNKNOWN)
        return
    if not geom_ok:
        _set(r, "uncertain", _ids_of(free), REASON_GEOMETRY_MISSING)
        return
    _set(r, "satisfied", _ids_of(free), REASON_FREE_OK)


def _verify_open(
    r: Requirement,
    stops: list[Any],
    route_present: bool,
    geom_ok: bool,
    at: datetime | None,
) -> None:
    """Whether the walk is inside opening hours at ``at``. The moment is an input, not
    a clock read: a verdict about "now" that changes between two identical requests is
    not a verdict. Without a moment the request cannot be proven either way.
    """
    if at is None:
        _set(r, "uncertain", [], REASON_OPEN_NOT_DATED)
        return
    if not route_present:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
        return
    when = local_at(at)
    opened: list[Any] = []
    closed: list[Any] = []
    unknown: list[Any] = []
    for stop in stops:
        verdict = opens_at(_field(stop, "opening_hours"), when)
        if verdict is None:
            unknown.append(stop)
        elif verdict:
            opened.append(stop)
        else:
            closed.append(stop)
    if closed:
        _set(r, "unmet", _ids_of(closed), REASON_OPEN_CLOSED)
        return
    if unknown:
        _set(r, "uncertain", _ids_of(unknown), REASON_OPEN_UNKNOWN)
        return
    if not geom_ok:
        _set(r, "uncertain", _ids_of(opened), REASON_GEOMETRY_MISSING)
        return
    _set(r, "satisfied", _ids_of(opened), REASON_OPEN_OK)


def _verify_step_free(r: Requirement, steps: StepsAlong | None, route_present: bool) -> None:
    """Whether the measured line runs over a stairway. The verdict is about the walk's
    own geometry — ``steps_along`` re-read it — never about the costing option that
    built it, so a route that still crosses a staircase is ``unmet`` how ever it was asked
    for. Without the stairway data the answer is ``uncertain``, not a claim.
    """
    if not route_present:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
        return
    if steps is None or not steps.measured:
        _set(r, "uncertain", [], REASON_STEPS_UNKNOWN)
        return
    if steps.steps:
        _set(r, "unmet", _ids_of(steps.steps), REASON_STEPS_ON_ROUTE)
        return
    _set(r, "satisfied", [], REASON_STEP_FREE_PROVEN)


def verify(  # noqa: PLR0913 — the verdict's whole input: route, line, and what was measured
    requirements: TripRequirements,
    plan: Any,
    geometry: Any,
    services_along: ServiceAlongEvidence | None = None,
    *,
    facts: CategoryFacts,
    at: datetime | None = None,
    steps_along: StepsAlong | None = None,
) -> list[Requirement]:
    """Statuses are written onto the requirement objects *in place* and the same
    list is returned; every requirement leaves resolved.

    ``at`` is the moment the walk is checked against, passed in rather than read off
    the clock: the same route verified twice against the same ``at`` answers the same.
    """
    codes = facts.codes
    stops = _route_stops(plan)
    trace = _plan_trace(plan)
    unroutable = _unroutable_ids(trace)
    geom_ok = geometry_ok(geometry)
    route_present = bool(stops)

    for r in requirements.requirements:
        if r.kind == "must_visit":
            _verify_must_visit(r, stops, route_present, unroutable, geom_ok)
        elif r.kind == "service":
            _verify_service(
                r,
                stops,
                route_present,
                geom_ok,
                codes,
                evidence=_evidence_for(r, services_along, codes),
            )
        elif r.kind == "interest":
            _verify_interest(r, stops, route_present, geom_ok, codes)
        elif r.kind == "avoid":
            _verify_avoid(r, stops, route_present, codes)
        elif r.kind == "free":
            _verify_free(r, stops, route_present, geom_ok)
        elif r.kind == "open":
            _verify_open(r, stops, route_present, geom_ok, at)
        elif r.kind == "step_free":
            _verify_step_free(r, steps_along, route_present)
        else:
            _set(r, "uncertain", [], REASON_CODE_UNSPECIFIED)

    return requirements.requirements


def verify_catalogue(
    requirements: TripRequirements,
    places: list[Any],
    *,
    facts: CategoryFacts,
    at: datetime | None = None,
) -> list[Requirement]:
    """Check a CATALOGUE (a list of places to choose from) against the request.

    A catalogue is a choice, so a fact it must carry is proven by *some* place holding
    it — one free place in the list answers «бесплатно», where a walk would have to be
    free stop by stop.
    """
    codes = facts.codes
    empty = not places
    for r in requirements.requirements:
        code = _canonical_code(r.code, codes) if r.code else None
        if r.kind == "must_visit":
            _catalogue_must_visit(r, places, empty=empty)
        elif r.kind == "free":
            _catalogue_free(r, places, empty=empty)
        elif r.kind == "open":
            _catalogue_open(r, places, empty=empty, at=at)
        elif r.kind == "step_free":
            _catalogue_step_free(r)
        elif not r.code:
            _set(r, "uncertain", [], REASON_CODE_UNSPECIFIED)
        elif code is None:
            _set(r, "uncertain", [], REASON_CODE_UNKNOWN)
        elif r.kind in ("service", "interest"):
            _catalogue_wanted(r, places, code, codes, empty=empty)
        else:
            _catalogue_avoid(r, places, code, codes, empty=empty)

    return requirements.requirements


def _catalogue_must_visit(r: Requirement, places: list[Any], *, empty: bool) -> None:
    """A named or id'd place the tourist asked to see, looked for in the list."""
    if r.place_id is None and not r.name:
        _set(r, "uncertain", [], REASON_MUST_VISIT_UNSPECIFIED)
        return
    match = _match_named(r, places)
    if match is not None:
        pid = _field(match, "id")
        _set(r, "satisfied", [pid] if isinstance(pid, int) else [], REASON_MUST_VISIT_IN_CATALOGUE)
    elif empty:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
    else:
        _set(r, "unmet", [], REASON_MUST_VISIT_ABSENT)


def _catalogue_wanted(
    r: Requirement, places: list[Any], code: str, codes: frozenset[str], *, empty: bool
) -> None:
    """A service or interest the catalogue is expected to contain somewhere."""
    matched = [p for p in places if _stop_matches_code(p, code, codes)]
    if matched:
        ids = [i for i in (_field(p, "id") for p in matched) if isinstance(i, int)]
        reason = (
            REASON_SERVICE_IN_CATALOGUE if r.kind == "service" else REASON_INTEREST_IN_CATALOGUE
        )
        _set(r, "satisfied", ids, reason)
    elif empty:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
    elif r.kind == "service":
        reason = REASON_HARD_SERVICE_ABSENT if r.strength == "hard" else REASON_SOFT_SERVICE_ABSENT
        _set(r, "unmet", [], reason)
    else:
        _set(r, "unmet", [], REASON_INTEREST_ABSENT)


def _catalogue_free(r: Requirement, places: list[Any], *, empty: bool) -> None:
    """The list has to hold a free place to choose; it does not have to hold only those."""
    free, paid, unknown = _by_admission(places)
    if free:
        _set(r, "satisfied", _ids_of(free), REASON_FREE_OK)
        return
    if empty:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
        return
    if unknown:
        _set(r, "uncertain", _ids_of(unknown), REASON_FREE_UNKNOWN)
        return
    _set(r, "unmet", _ids_of(paid), REASON_FREE_PAID)


def _catalogue_open(r: Requirement, places: list[Any], *, empty: bool, at: datetime | None) -> None:
    """The list has to hold somewhere open at that hour to choose."""
    if at is None:
        _set(r, "uncertain", [], REASON_OPEN_NOT_DATED)
        return
    when = local_at(at)
    opened: list[Any] = []
    closed: list[Any] = []
    unknown: list[Any] = []
    for place in places:
        verdict = opens_at(_field(place, "opening_hours"), when)
        if verdict is None:
            unknown.append(place)
        elif verdict:
            opened.append(place)
        else:
            closed.append(place)
    if opened:
        _set(r, "satisfied", _ids_of(opened), REASON_OPEN_OK)
        return
    if empty:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
        return
    if unknown:
        _set(r, "uncertain", _ids_of(unknown), REASON_OPEN_UNKNOWN)
        return
    _set(r, "unmet", _ids_of(closed), REASON_OPEN_CLOSED)


def _catalogue_step_free(r: Requirement) -> None:
    """A catalogue is a set of places to choose from; a walk's stairways are not a
    property of that list, so nothing here can prove the choice step-free.
    """
    _set(r, "uncertain", [], REASON_STEPS_UNKNOWN)


def _catalogue_avoid(
    r: Requirement, places: list[Any], code: str, codes: frozenset[str], *, empty: bool
) -> None:
    """Something the tourist asked to keep away from — satisfied only when absent."""
    offenders = [p for p in places if _stop_matches_code(p, code, codes)]
    if offenders:
        ids = [i for i in (_field(p, "id") for p in offenders) if isinstance(i, int)]
        _set(r, "unmet", ids, REASON_AVOID_VIOLATED)
    elif empty:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
    else:
        _set(r, "satisfied", [], REASON_AVOID_OK)


def _key(r: Requirement) -> str:
    """A short, localizable handle for a requirement (code, else name, else kind)."""
    return r.code or r.name or r.kind


def verify_summary(requirements: TripRequirements) -> dict[str, Any]:
    """Counts and handles per status, plus the overall plan status."""
    buckets: dict[str, list[str]] = {
        "satisfied": [],
        "unmet": [],
        "uncertain": [],
        "pending": [],
    }
    for r in requirements.requirements:
        buckets.setdefault(r.status, []).append(_key(r))
    return {"status": overall_status(requirements), **buckets}


def overall_status(
    requirements: TripRequirements,
) -> Literal["ready", "infeasible", "degraded", "pending"]:
    """Overall fate of the request."""
    reqs = requirements.requirements
    if not reqs:
        return "ready"

    hard = requirements.hard()
    if any(r.status == "unmet" and r.reason in INFEASIBLE_REASONS for r in hard):
        return "infeasible"
    if any(r.status in ("unmet", "uncertain") for r in hard):
        return "degraded"
    if any(r.status == "pending" for r in reqs):
        return "pending"
    if any(r.status == "uncertain" for r in reqs):
        return "degraded"
    return "ready"

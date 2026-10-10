"""Step 7b — Independent requirement verifier."""

from __future__ import annotations

import re
from typing import Any, Literal, NamedTuple

from domain import taxonomy
from domain.requirements import (
    REASON_MUST_VISIT_OUTSIDE as REASON_MUST_VISIT_OUTSIDE_CODE,
    Requirement,
    TripRequirements,
)

__all__ = [
    "INFEASIBLE_REASONS",
    "REASON_CODES",
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

REASON_CODE_UNSPECIFIED = "category_code_unspecified"
REASON_CODE_UNKNOWN = "category_code_unknown"

REASON_ROUTE_MISSING = "route_missing"
REASON_GEOMETRY_MISSING = "geometry_missing"

class ServiceAlongEvidence(NamedTuple):
    """Three states: nothing supplied keeps the older reading, ``measured=True`` is
    evidence, ``measured=False`` is ``uncertain``, never ``unmet``.
    """

    measured: bool
    by_code: dict[str, list[dict]]


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


def _plan_trace(plan: Any) -> dict:
    if isinstance(plan, dict):
        trace = plan.get("trace")
        return trace if isinstance(trace, dict) else {}
    trace = getattr(plan, "trace", None)
    return trace if isinstance(trace, dict) else {}


def _unroutable_ids(trace: dict) -> set[int]:
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
    """True when ``geometry`` is a usable line: ≥2 coordinates.

    None, ``{}``, ``{"coordinates": []}`` and a bare Point all return False.
    """
    if geometry is None or not isinstance(geometry, dict):
        return False

    gtype = geometry.get("type")
    if gtype == "Feature":
        return geometry_ok(geometry.get("geometry"))
    if gtype == "FeatureCollection":
        feats = geometry.get("features") or []
        return any(
            geometry_ok(f.get("geometry")) for f in feats if isinstance(f, dict)
        )
    if gtype == "Point":
        return False

    coords = geometry.get("coordinates")
    return _coord_count(coords) >= 2


def _coord_count(coords: Any) -> int:
    """Number of positions in a coordinate array (recurses into Multi*)."""
    if not isinstance(coords, (list, tuple)) or not coords:
        return 0
    first = coords[0]
    if isinstance(first, (list, tuple)):
        return max((_coord_count(c) for c in coords), default=0)
    return len(coords)


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _norm_name(name: str | None) -> str:
    text = (name or "").strip().lower().replace("ё", "е")
    text = _PUNCT_RE.sub(" ", text)
    return " ".join(text.split())


def _norm_cat(value: str | None) -> str:
    return (value or "").strip().lower().replace("ё", "е")


def _canonical_code(code: str | None) -> str | None:
    """Resolve a requirement code to a canonical taxonomy code, or None."""
    if not code:
        return None
    try:
        if code in taxonomy.all_codes():
            return code
        return taxonomy.resolve_code(code)
    except Exception:  # pragma: no cover — taxonomy file missing / bad data
        return None


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read a stop's field whether the plan arrived as objects or as mappings.

    Reading both spellings removes a class of silent wrong verdicts.
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _stop_matches_code(stop: Any, code: str) -> bool:
    """Does a route stop belong to the canonical category ``code``?"""
    stop_cat = _norm_cat(_field(stop, "category"))
    norm_code = _norm_cat(code)
    if not stop_cat or not norm_code:
        return False
    if stop_cat == norm_code or norm_code in stop_cat or stop_cat in norm_code:
        return True
    resolved = _canonical_code(stop_cat)
    return resolved is not None and _norm_cat(resolved) == norm_code


def _set(requirement: Requirement, status: Status, place_ids: list[int], reason: str) -> None:
    requirement.status = status
    requirement.place_ids = list(place_ids)
    requirement.reason = reason


def _match_named(r: Requirement, stops: list[Any]) -> Any | None:
    """The stop proving a named/numbered must-visit, or None.

    Identity first (a real place id), then the normalised name.
    """
    if r.place_id is not None:
        for stop in stops:
            if _field(stop, "id") == r.place_id:
                return stop
    if r.name:
        wanted = _norm_name(r.name)
        for stop in stops:
            if wanted and _norm_name(_field(stop, "name")) == wanted:
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
    r: Requirement, services_along: ServiceAlongEvidence | None
) -> tuple[bool | None, list[dict] | None]:
    """``(measured_ok, items)`` for this requirement.

    ``measured_ok`` is ``None`` when the caller measured nothing at all.
    """
    if services_along is None:
        return None, None
    if not services_along.measured:
        return False, None
    if not r.code:
        return True, None
    code = _canonical_code(r.code)
    if code is None:
        return True, None
    return True, services_along.by_code.get(code, [])


def _verify_service(
    r: Requirement,
    stops: list[Any],
    route_present: bool,
    geom_ok: bool,
    evidence: tuple[bool | None, list[dict] | None] = (None, None),
) -> None:
    measured_ok, measured = evidence
    if not r.code:
        _set(r, "uncertain", [], REASON_CODE_UNSPECIFIED)
        return
    code = _canonical_code(r.code)
    if code is None:
        _set(r, "uncertain", [], REASON_CODE_UNKNOWN)
        return

    matched = [s for s in stops if _stop_matches_code(s, code)]
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
    reason = (
        REASON_HARD_SERVICE_ABSENT if r.strength == "hard" else REASON_SOFT_SERVICE_ABSENT
    )
    _set(r, "unmet", [], reason)


def _verify_interest(
    r: Requirement,
    stops: list[Any],
    route_present: bool,
    geom_ok: bool,
) -> None:
    if not r.code:
        _set(r, "uncertain", [], REASON_CODE_UNSPECIFIED)
        return
    code = _canonical_code(r.code)
    if code is None:
        _set(r, "uncertain", [], REASON_CODE_UNKNOWN)
        return

    matched = [s for s in stops if _stop_matches_code(s, code)]
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


def _verify_avoid(r: Requirement, stops: list[Any], route_present: bool) -> None:
    if not r.code:
        _set(r, "uncertain", [], REASON_CODE_UNSPECIFIED)
        return
    code = _canonical_code(r.code)
    if code is None:
        _set(r, "uncertain", [], REASON_CODE_UNKNOWN)
        return

    offenders = [s for s in stops if _stop_matches_code(s, code)]
    if offenders:
        ids = [i for i in (_field(s, "id") for s in offenders) if isinstance(i, int)]
        _set(r, "unmet", ids, REASON_AVOID_VIOLATED)
        return
    if not route_present:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
        return
    _set(r, "satisfied", [], REASON_AVOID_OK)


def verify(
    requirements: TripRequirements,
    plan: Any,
    geometry: Any,
    services_along: ServiceAlongEvidence | None = None,
) -> list[Requirement]:
    """Statuses are written onto the requirement objects *in place* and the same
    list is returned; every requirement leaves resolved.
    """
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
                r, stops, route_present, geom_ok, _evidence_for(r, services_along)
            )
        elif r.kind == "interest":
            _verify_interest(r, stops, route_present, geom_ok)
        elif r.kind == "avoid":
            _verify_avoid(r, stops, route_present)
        else:  # pragma: no cover — RequirementKind is closed
            _set(r, "uncertain", [], REASON_CODE_UNSPECIFIED)

    return requirements.requirements


def verify_catalogue(
    requirements: TripRequirements, places: list[Any]
) -> list[Requirement]:
    """Check a CATALOGUE (a list of places to choose from) against the request.

    Satisfaction reasons are the `*_in_catalogue` codes — no verdict claims "on the route".
    """
    empty = not places
    for r in requirements.requirements:
        code = _canonical_code(r.code) if r.code else None

        if r.kind == "must_visit":
            if r.place_id is None and not r.name:
                _set(r, "uncertain", [], REASON_MUST_VISIT_UNSPECIFIED)
                continue
            match = _match_named(r, places)
            if match is not None:
                pid = _field(match, "id")
                ids = [pid] if isinstance(pid, int) else []
                _set(r, "satisfied", ids, REASON_MUST_VISIT_IN_CATALOGUE)
            elif empty:
                _set(r, "uncertain", [], REASON_ROUTE_MISSING)
            else:
                _set(r, "unmet", [], REASON_MUST_VISIT_ABSENT)
            continue

        if not r.code:
            _set(r, "uncertain", [], REASON_CODE_UNSPECIFIED)
            continue
        if code is None:
            _set(r, "uncertain", [], REASON_CODE_UNKNOWN)
            continue

        if r.kind in ("service", "interest"):
            matched = [p for p in places if _stop_matches_code(p, code)]
            if matched:
                ids = [i for i in (_field(p, "id") for p in matched) if isinstance(i, int)]
                reason = (
                    REASON_SERVICE_IN_CATALOGUE
                    if r.kind == "service"
                    else REASON_INTEREST_IN_CATALOGUE
                )
                _set(r, "satisfied", ids, reason)
            elif empty:
                _set(r, "uncertain", [], REASON_ROUTE_MISSING)
            elif r.kind == "service":
                _set(
                    r,
                    "unmet",
                    [],
                    REASON_HARD_SERVICE_ABSENT
                    if r.strength == "hard"
                    else REASON_SOFT_SERVICE_ABSENT,
                )
            else:
                _set(r, "unmet", [], REASON_INTEREST_ABSENT)
            continue

        offenders = [p for p in places if _stop_matches_code(p, code)]
        if offenders:
            ids = [i for i in (_field(p, "id") for p in offenders) if isinstance(i, int)]
            _set(r, "unmet", ids, REASON_AVOID_VIOLATED)
        elif empty:
            _set(r, "uncertain", [], REASON_ROUTE_MISSING)
        else:
            _set(r, "satisfied", [], REASON_AVOID_OK)

    return requirements.requirements


def _key(r: Requirement) -> str:
    """A short, localizable handle for a requirement (code, else name, else kind)."""
    return r.code or r.name or r.kind


def verify_summary(requirements: TripRequirements) -> dict:
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
    if any(
        r.status == "unmet" and r.reason in INFEASIBLE_REASONS for r in hard
    ):
        return "infeasible"
    if any(r.status in ("unmet", "uncertain") for r in hard):
        return "degraded"
    if any(r.status == "pending" for r in reqs):
        return "pending"
    if any(r.status == "uncertain" for r in reqs):
        return "degraded"
    return "ready"

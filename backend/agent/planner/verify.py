"""Step 7b — Independent requirement verifier.

``verify(requirements, plan, geometry)`` takes the *built* route and decides,
per requirement, one of four fates:

  * ``satisfied`` — a real place on the route (and a real geometry) proves it;
                    the proving ``place_ids`` are listed.
  * ``unmet``     — the data exists (a named place, a category the domain knows)
                    but the route does not honour the requirement.
  * ``uncertain`` — the data needed to decide does not exist (no geometry from
                    Valhalla, an unknown category code, an empty route). Never
                    reported as satisfied.
  * ``pending``   — verify() has not run for this requirement.

The verifier is deliberately independent from the optimizer: it re-reads the
final route and the Valhalla geometry, it never trusts the optimizer's claim
that a hard condition was met. An LLM may propose the meaning of a requirement
and may re-plan after a failure, but it cannot overrule ``unmet``.

Reasons are MACHINE STRINGS (``must_visit_absent``, ``hard_service_absent``,
``geometry_missing`` …); the API/UI layer localizes them. Nothing here returns
a Russian sentence.

Owned by workstream W3 — see docs/specs/002-grodno-guide-rebuild/tasks.md.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple, Literal

from .. import taxonomy
from ..requirements import (
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
    "verify_summary",
]

# ── Machine-readable reason codes ─────────────────────────────────────────────
# Localization happens in the API layer; these strings are the contract.

REASON_MUST_VISIT_OK = "must_visit_on_route"
REASON_MUST_VISIT_ABSENT = "must_visit_absent"
REASON_MUST_VISIT_UNROUTABLE = "must_visit_unroutable"
REASON_MUST_VISIT_UNSPECIFIED = "must_visit_unspecified"
# Re-exported so callers here read like the rest of the vocabulary; the string
# itself lives in requirements.py, next to the contract that sets it.
REASON_MUST_VISIT_OUTSIDE = REASON_MUST_VISIT_OUTSIDE_CODE

REASON_SERVICE_OK = "service_on_route"
#: A service of the requested kind lies beside the line — measured, not assumed.
#: The window is the one the measurement used; walking time to it is a detour and
#: is never claimed here.
REASON_SERVICE_ALONG_ROUTE = "service_along_route"
#: The measurement itself failed (bad shape, database down). That is «мы не
#: знаем», not «нет»: an unmet claim needs evidence we did not get.
REASON_SERVICE_NOT_MEASURED = "service_not_measured"
REASON_HARD_SERVICE_ABSENT = "hard_service_absent"
REASON_SOFT_SERVICE_ABSENT = "soft_service_absent"

REASON_INTEREST_OK = "interest_on_route"
REASON_INTEREST_ABSENT = "interest_absent"

REASON_AVOID_OK = "avoid_honoured"
REASON_AVOID_VIOLATED = "avoid_violated"

REASON_CODE_UNSPECIFIED = "category_code_unspecified"
REASON_CODE_UNKNOWN = "category_code_unknown"

REASON_ROUTE_MISSING = "route_missing"
REASON_GEOMETRY_MISSING = "geometry_missing"

class ServiceAlongEvidence(NamedTuple):
    """What the caller measured beside the line, and whether it managed to.

    Three states, kept apart on purpose — collapsing any two of them would make
    the verifier claim something nobody checked:

    * nothing supplied (the parameter is ``None``): the caller does not measure,
      so service requirements keep their older «on the route or unmet» reading;
    * ``measured=True`` with ``by_code``: the measurement ran. A code that is
      absent from the mapping means «рядом нет» — that is evidence, and it is
      reported as ``unmet``;
    * ``measured=False``: the measurement itself failed (a broken shape, the
      database down). That is «мы не знаем» → ``uncertain``, never ``unmet``.
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
        REASON_AVOID_OK,
        REASON_AVOID_VIOLATED,
        REASON_CODE_UNSPECIFIED,
        REASON_CODE_UNKNOWN,
        REASON_ROUTE_MISSING,
        REASON_GEOMETRY_MISSING,
    }
)

# Reasons that mean a hard requirement cannot be honoured at all: the request
# is infeasible, not merely degraded.
INFEASIBLE_REASONS: frozenset[str] = frozenset(
    {
        REASON_MUST_VISIT_UNROUTABLE,
        REASON_MUST_VISIT_ABSENT,
        REASON_MUST_VISIT_OUTSIDE,
        REASON_HARD_SERVICE_ABSENT,
    }
)

Status = Literal["pending", "satisfied", "unmet", "uncertain"]


# ─────────────────────────────────────────────────────────────────────────────
# Input normalisation (plan / geometry may be any of several shapes)
# ─────────────────────────────────────────────────────────────────────────────

def _route_stops(plan: Any) -> list[Any]:
    """The ordered stops of the plan, from whatever shape the caller passed.

    Accepts a ``ValidatedPlan`` (``.route``), a plain ``list[Candidate]``, or a
    ``{"route": [...], "trace": {...}}`` mapping. Anything else is treated as
    "no route".
    """
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
    """Stop ids the pruner reported as unroutable in this order.

    ``cost.prune_unroutable_stops`` returns machine reasons; the integration
    step records them under ``trace["unroutable_stops"]`` (list of ids or of
    ``{"id": ..., "reason": ...}`` mappings). We accept both, plus the legacy
    ``pruned_stops`` key.
    """
    out: set[int] = set()
    for key in ("unroutable_stops", "pruned_stops"):
        for item in trace.get(key) or []:
            if isinstance(item, int):
                pid: Any = item
            elif isinstance(item, dict):
                pid = item.get("id")
            else:
                pid = item.id  # a PrunedStop / Candidate
            if isinstance(pid, int):
                out.add(pid)
    return out


def geometry_ok(geometry: Any) -> bool:
    """True when ``geometry`` is a usable line: ≥2 coordinates.

    None, ``{}``, ``{"coordinates": []}`` and a bare Point all return False.
    A missing/empty geometry means Valhalla could not confirm the route is
    walkable, so nothing dependent on it may be called ``satisfied``.
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


# ─────────────────────────────────────────────────────────────────────────────
# Category / name normalisation
# ─────────────────────────────────────────────────────────────────────────────

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

    ``verify`` documents accepting a ``{"route": [...]}`` mapping, but the
    matching below used to read attributes only — so a mapping plan matched
    *nothing* and every requirement came back unmet (or, for ``avoid``,
    "honoured") while looking perfectly verified. Reading both spellings removes
    that whole class of silent wrong verdicts.
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
    # Compound/free-form DB categories ("кафе-кондитерская") resolve via taxonomy.
    resolved = _canonical_code(stop_cat)
    return resolved is not None and _norm_cat(resolved) == norm_code


# ─────────────────────────────────────────────────────────────────────────────
# Per-kind verification
# ─────────────────────────────────────────────────────────────────────────────

def _set(requirement: Requirement, status: Status, place_ids: list[int], reason: str) -> None:
    requirement.status = status
    requirement.place_ids = list(place_ids)
    requirement.reason = reason


def _verify_must_visit(
    r: Requirement,
    stops: list[Any],
    route_present: bool,
    unroutable: set[int],
    geom_ok: bool,
) -> None:
    # A place outside the region is not "missing from the plan" — it can never
    # be in it, and the look-alike the name matcher liked (a Lida cathedral for
    # a Vilnius one) must not be allowed to satisfy it either. The deterministic
    # layer set this reason before planning; nothing in the plan overturns it.
    if r.reason == REASON_MUST_VISIT_OUTSIDE:
        _set(r, "unmet", [], REASON_MUST_VISIT_OUTSIDE)
        return

    if r.place_id is None and not r.name:
        _set(r, "uncertain", [], REASON_MUST_VISIT_UNSPECIFIED)
        return

    match = None
    if r.place_id is not None:
        for stop in stops:
            if _field(stop, "id") == r.place_id:
                match = stop
                break
    if match is None and r.name:
        wanted = _norm_name(r.name)
        for stop in stops:
            if wanted and _norm_name(_field(stop, "name")) == wanted:
                match = stop
                break

    if match is not None:
        pid = _field(match, "id")
        ids = [pid] if isinstance(pid, int) else []
        if geom_ok:
            _set(r, "satisfied", ids, REASON_MUST_VISIT_OK)
        else:
            # The stop is in the plan, but without geometry we cannot prove the
            # route actually reaches it — "uncertain", never "satisfied".
            _set(r, "uncertain", ids, REASON_GEOMETRY_MISSING)
        return

    if not route_present:
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
        return
    if r.place_id is not None and r.place_id in unroutable:
        _set(r, "unmet", [], REASON_MUST_VISIT_UNROUTABLE)
        return
    _set(r, "unmet", [], REASON_MUST_VISIT_ABSENT)


def _evidence_for(
    r: Requirement, services_along: ServiceAlongEvidence | None
) -> tuple[bool | None, list[dict] | None]:
    """``(measured_ok, items)`` for this requirement.

    ``measured_ok`` is ``None`` when the caller measured nothing at all — the
    older semantics — so a caller that does not measure keeps them.
    """
    if services_along is None:
        return None, None
    if not services_along.measured:
        return False, None
    if not r.code:
        return True, None  # the code check reports that itself
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

    # No stop of this kind — but a café beside the line is exactly what «кофе по
    # пути» asked for. The caller measured it; we only read the measurement.
    if measured_ok is False and r.strength == "hard":
        # The caller owns the measurement; when it could not run one, saying
        # «нет» would be a claim we cannot support.
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
        # "Nothing forbidden is present" is vacuous with no route to inspect.
        _set(r, "uncertain", [], REASON_ROUTE_MISSING)
        return
    _set(r, "satisfied", [], REASON_AVOID_OK)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def verify(
    requirements: TripRequirements,
    plan: Any,
    geometry: Any,
    services_along: ServiceAlongEvidence | None = None,
) -> list[Requirement]:
    """Check a built route against the user's requirements.

    ``plan``      — a ``ValidatedPlan``, a ``list[Candidate]``, or a
                    ``{"route": [...], "trace": {...}}`` mapping.
    ``geometry``  — the final Valhalla shape (GeoJSON LineString/Feature) or
                    None when Valhalla could not draw the tour.
    ``services_along`` — measured evidence, supplied by the caller (which owns
                    the database). This function stays free of I/O and of
                    models: it decides on the evidence it is handed, nothing
                    else. When a service requirement has no stop of its kind,
                    this evidence is what turns «не выполнено» into «есть по
                    пути»; when the caller could not measure, the requirement
                    becomes ``uncertain`` (``service_not_measured``).

    Statuses are written onto the requirement objects *in place* and the same
    list is returned, so ``requirements.failed_hard()`` / ``is_ready()`` and the
    return value agree. Every requirement leaves this function resolved
    (``satisfied`` / ``unmet`` / ``uncertain``) — never pending.

    A missing or empty geometry is not an error: requirements that would need
    the line to prove reachability become ``uncertain`` (``geometry_missing``),
    never ``satisfied``.
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
    """Overall fate of the request.

    * ``infeasible`` — a hard requirement is unmet for a structural reason
      (a mandatory place absent or unroutable, a mandatory service missing).
    * ``degraded``   — no hard requirement failed, but some are unproven.
    * ``pending``    — verify() has not run.
    * ``ready``      — every requirement is resolved and every hard one proven.
    """
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

"""The compliance checks and their verdicts."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from core.hours import parse_moment
from db.seed.geofence import inside_project_area
from quality.bench.analysis import norm_name
from quality.bench.cases import _norm_code
from quality.bench.config import (
    CANONICAL_CATEGORIES,
    CANONICAL_CODES_NORM,
    CHECK_API_ERROR,
    CHECK_FORBIDDEN_CATEGORY_PRESENT,
    CHECK_MISSING_MANDATORY_CATEGORY,
    CHECK_MISSING_NAMED_PLACE,
    CHECK_MOMENT_APPLIED,
    CHECK_OUT_OF_REGION_POINT,
    CHECK_OVER_BUDGET,
    CHECK_PARITY,
    CHECK_PRIORITY,
    CHECK_REQUIREMENT_STATUS,
    CHECK_REQUIREMENT_UNMET,
    CHECK_RESULT_MODE,
    CHECK_TOO_FEW_PLACES,
    CHECK_WRONG_STATUS,
    _is_num,
)
from quality.bench.models import ComplianceVerdict, GoldenCase
from routing.models import Requirement, TripRequirements
from routing.verify import overall_status


def _status_from_requirements(requirements: list[Any]) -> str:
    """The plan's status, decided by the LIVE verifier — `routing.verify.overall_status`."""
    rebuilt: list[Requirement] = []
    for r in requirements:
        if not isinstance(r, dict) or not isinstance(r.get("kind"), str):
            continue
        try:
            rebuilt.append(
                Requirement(
                    kind=r["kind"],
                    strength=r.get("strength") or "soft",
                    code=r.get("code"),
                    name=r.get("name"),
                    status=r.get("status") or "pending",
                    place_ids=list(r.get("place_ids") or []),
                    reason=r.get("detail"),
                )
            )
        except Exception:
            continue
    return overall_status(TripRequirements(requirements=rebuilt))


def derive_status(
    raw: dict[str, Any] | None,
    *,
    http_status: int | None = None,
    api_error: str | None = None,
) -> tuple[str, str]:
    """(status, source) for one response, using the best evidence available."""
    if api_error or (http_status is not None and http_status >= HTTPStatus.BAD_REQUEST):
        return "error", "api_error" if api_error else "http_status"
    raw = raw or {}
    explicit = raw.get("status")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower(), "response.status"
    requirements = raw.get("requirements")
    if isinstance(requirements, list) and requirements:
        return _status_from_requirements(requirements), "response.requirements"
    if not (raw.get("points") or []):
        return "infeasible", "derived:empty_plan"
    return "ready", "derived:points"


def codes_in_name(name: str) -> set[str]:
    """Canonical category codes occurring as a word in a POI name."""
    tokens = {t.replace("ё", "е") for t in norm_name(name).split()}
    return {code for code in CANONICAL_CODES_NORM if code in tokens}


def _canon_codes(codes) -> list[str]:
    """Canonical spelling of normalised codes, for anything a human reads."""
    return [CANONICAL_CATEGORIES.get(c, c) for c in codes]


def stop_category_codes(stop: dict[str, Any]) -> set[str]:
    """The canonical codes one returned stop counts as."""
    category = _norm_code(stop.get("category"))
    if category:
        return {category}
    return codes_in_name(stop.get("name", ""))


def _ok(detail: str = "", **extra) -> dict[str, Any]:
    return {"ok": True, "detail": detail, **extra}


def _fail(detail: str, **extra) -> dict[str, Any]:
    return {"ok": False, "detail": detail, **extra}


def _unverified(detail: str, **extra) -> dict[str, Any]:
    return {"ok": True, "unverified": True, "detail": detail, **extra}


def response_total_minutes(
    raw: dict[str, Any] | None, points: list[dict[str, Any]]
) -> float | None:
    """Total route minutes: `budget.total_minutes`, or walk + visits as a fallback."""
    raw = raw or {}
    budget = raw.get("budget") or {}
    total = budget.get("total_minutes")
    if _is_num(total):
        return float(total)
    summary = raw.get("summary") or {}
    walk_s = summary.get("time_seconds")
    visits = sum(p.get("visit_minutes") or 0 for p in points if _is_num(p.get("visit_minutes")))
    if _is_num(walk_s):
        return float(walk_s) / 60.0 + float(visits)
    return None


def evaluate_compliance(
    case: GoldenCase,
    raw: dict[str, Any] | None,
    *,
    http_status: int | None = None,
    api_error: str | None = None,
) -> ComplianceVerdict:
    """Every expectation of one golden case against one response."""
    exp = case.expectations
    raw = raw or {}
    points = [p for p in (raw.get("points") or []) if isinstance(p, dict)]
    status, source = derive_status(raw, http_status=http_status, api_error=api_error)
    checks: dict[str, dict[str, Any]] = {}

    checks[CHECK_WRONG_STATUS] = _status_check(status, source, exp)
    checks[CHECK_API_ERROR] = _api_error_check(status, http_status, api_error)
    checks[CHECK_TOO_FEW_PLACES] = _too_few_places_check(points, exp)

    seen, stop_codes = _seen_codes(points, raw)
    checks[CHECK_MISSING_MANDATORY_CATEGORY] = _mandatory_check(exp, seen)
    checks[CHECK_FORBIDDEN_CATEGORY_PRESENT] = _forbidden_check(exp, stop_codes)
    if exp.get("must_contain_names"):
        checks[CHECK_MISSING_NAMED_PLACE] = _named_check(exp, points)
    if exp.get("must_satisfy_requirement_kinds"):
        checks[CHECK_REQUIREMENT_UNMET] = _requirement_check(raw, exp)
    if exp.get("requirement_status"):
        checks[CHECK_REQUIREMENT_STATUS] = _requirement_status_check(raw, exp)
    if case.filters.get("at"):
        checks[CHECK_MOMENT_APPLIED] = _moment_check(raw, case.filters.get("at"))
    if exp.get("in_region"):
        checks[CHECK_OUT_OF_REGION_POINT] = _region_check(points)
    checks[CHECK_OVER_BUDGET] = _budget_check(raw, points, exp)
    checks[CHECK_RESULT_MODE] = _result_mode_check(raw, exp)

    failed = [code for code in CHECK_PRIORITY if checks.get(code, {}).get("ok") is False]
    reason = failed[0] if failed else "ok"
    detail = checks[reason]["detail"] if failed else ""
    return ComplianceVerdict(
        case_id=case.id,
        passed=not failed,
        reason=reason,
        detail=detail,
        status=status,
        status_source=source,
        checks=checks,
    )


def _status_check(status: str, source: str, exp: dict[str, Any]) -> dict[str, Any]:
    """The status the case will accept, and the one it got."""
    expected_status = [str(s).strip().lower() for s in exp["expected_status"]]
    if status in expected_status:
        return _ok(f"{status} ({source}) is in {expected_status}")
    return _fail(f"status {status} ({source}) is not one of {expected_status}")


def _api_error_check(status: str, http_status: int | None, api_error: str | None) -> dict[str, Any]:
    """An error status is the failure; a 4xx the case expected is an honest refusal."""
    if status == "error":
        return _fail(api_error or f"the backend failed with HTTP {http_status} — no plan")
    if api_error or (http_status is not None and http_status >= HTTPStatus.BAD_REQUEST):
        return _ok(
            f"the backend refused the request (HTTP {http_status}) — an honest "
            f"refusal, judged by the status check"
        )
    return _ok("the backend answered with a plan")


def _too_few_places_check(points: list[dict[str, Any]], exp: dict[str, Any]) -> dict[str, Any]:
    """How many stops came back, against the minimum this case allows."""
    allow_empty = bool(exp.get("allow_empty"))
    min_places = exp.get("min_places")
    if min_places is None:
        min_places = 0 if allow_empty else 1
    if len(points) >= min_places:
        return _ok(f"{len(points)} stop(s) returned (>= {min_places})")
    detail = f"{len(points)} stop(s) returned, at least {min_places} required"
    if not points and allow_empty:
        detail = "empty plan, which this case allows"
    return _fail(detail)


def _seen_codes(points: list[dict[str, Any]], raw: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Every category the plan shows, and those of its stops alone."""
    seen: set[str] = set()
    for stop in points:
        seen |= stop_category_codes(stop)
    stop_codes = set(seen)
    for req in raw.get("requirements") or []:
        if not isinstance(req, dict) or req.get("status") != "satisfied":
            continue
        code = req.get("code")
        if isinstance(code, str) and code.strip():
            seen.add(_norm_code(code))
    return seen, stop_codes


def _mandatory_check(exp: dict[str, Any], seen: set[str]) -> dict[str, Any]:
    """The categories the case insists on, against the ones the plan shows."""
    wanted = [_norm_code(c) for c in exp["must_contain_categories"]]
    missing = [c for c in wanted if c not in seen]
    evidence = {"missing": _canon_codes(missing), "seen": sorted(_canon_codes(seen))}
    if not missing:
        return _ok(f"every mandatory category is present {_canon_codes(wanted)}", **evidence)
    return _fail(
        f"missing mandatory category(ies) {evidence['missing']}"
        f"; the plan has {evidence['seen'] or 'no categories'}",
        **evidence,
    )


def _forbidden_check(exp: dict[str, Any], stop_codes: set[str]) -> dict[str, Any]:
    """The categories the case rules out, against the stops themselves."""
    forbidden = [_norm_code(c) for c in exp["must_not_contain_categories"]]
    present = [c for c in forbidden if c in stop_codes]
    if not present:
        return _ok(f"no forbidden category present {_canon_codes(forbidden)}")
    return _fail(
        f"forbidden category(ies) present {[CANONICAL_CATEGORIES.get(c, c) for c in present]}"
    )


def _named_check(exp: dict[str, Any], points: list[dict[str, Any]]) -> dict[str, Any]:
    """The places the case names, against the names the plan carries."""
    names = exp.get("must_contain_names") or []
    missing_names = [
        n for n in names if not any(_norm_code(n) in norm_name(p.get("name", "")) for p in points)
    ]
    if not missing_names:
        return _ok(f"every named place is on the plan {names}")
    return _fail(
        f"missing named place(s) {missing_names}; plan names {[p.get('name') for p in points][:6]}"
    )


def _requirement_check(raw: dict[str, Any], exp: dict[str, Any]) -> dict[str, Any]:
    """Whether the kinds the case names were read out of the query and satisfied.

    The answer comes from the wire's own reading (`interpretation.requirements`), so
    the check sees what the parser understood, not what the case hoped for.
    """
    wanted = [str(k) for k in exp.get("must_satisfy_requirement_kinds") or []]
    interpretation = raw.get("interpretation")
    if not isinstance(interpretation, dict):
        return _unverified(f"the response carries no interpretation to read {wanted} from")
    signals = [s for s in (interpretation.get("requirements") or []) if isinstance(s, dict)]
    declared = {str(s.get("kind")): str(s.get("status")) for s in signals}
    missing = [k for k in wanted if k not in declared]
    if missing:
        return _fail(f"the reading declared no {missing} requirement; it read {sorted(declared)}")
    unmet = [k for k in wanted if declared[k] == "unmet"]
    if unmet:
        return _fail(f"the plan left {unmet} unmet; it read {declared}")
    return _ok(f"read and met {wanted}: {declared}")


def _requirement_status_check(raw: dict[str, Any], exp: dict[str, Any]) -> dict[str, Any]:
    """The exact statuses a case demands. `must_satisfy_requirement_kinds` passes on
    `uncertain`, so a condition that has to be PROVEN needs this one."""
    wanted = {str(k): str(v) for k, v in (exp.get("requirement_status") or {}).items()}
    interpretation = raw.get("interpretation")
    if not isinstance(interpretation, dict):
        return _unverified(f"the response carries no interpretation to read {sorted(wanted)} in")
    declared = {
        str(s.get("kind")): str(s.get("status"))
        for s in (interpretation.get("requirements") or [])
        if isinstance(s, dict)
    }
    wrong = {k: declared.get(k, "absent") for k, v in wanted.items() if declared.get(k) != v}
    if wrong:
        return _fail(f"expected {wanted}, read {wrong}")
    return _ok(f"read {sorted(wanted)} as asked")


def _moment_check(raw: dict[str, Any], chosen: Any) -> dict[str, Any]:
    """Whether the answer was checked against the moment the case asked for."""
    interpretation = raw.get("interpretation")
    echoed = interpretation.get("at") if isinstance(interpretation, dict) else None
    if not echoed:
        return _fail("the response does not say which moment it was checked against")
    if parse_moment(str(echoed)) != parse_moment(str(chosen)):
        return _fail(f"checked against {echoed}, the case asked for {chosen}")
    return _ok(f"checked against {echoed}")


def _region_check(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether every stop sits inside the region the product serves."""
    offenders: list[str] = []
    for stop in points:
        lat, lon = stop.get("lat"), stop.get("lon")
        if not (_is_num(lat) and _is_num(lon)):
            offenders.append(f"{stop.get('name')!r} has no coordinates")
        elif not inside_project_area(float(lat), float(lon)):
            offenders.append(f"{stop.get('name')!r} at {lat:.4f},{lon:.4f} is outside Grodno ADM1")
    if not offenders:
        return _ok(f"all {len(points)} point(s) inside Grodno ADM1")
    return _fail("; ".join(offenders[:4]))


def _budget_check(
    raw: dict[str, Any], points: list[dict[str, Any]], exp: dict[str, Any]
) -> dict[str, Any]:
    """The total the response reports, against the cap the case states."""
    cap = exp.get("max_total_minutes")
    if cap is None:
        return _ok("no time cap in this case")
    total = response_total_minutes(raw, points)
    if total is None:
        return _unverified(f"cap {cap} min stated but the response reports no total")
    if total > cap + 1e-9:
        return _fail(f"total {total:.1f} min exceeds the {cap} min cap")
    return _ok(f"total {total:.1f} min <= {cap} min cap")


def _result_mode_check(raw: dict[str, Any], exp: dict[str, Any]) -> dict[str, Any]:
    """The result mode the response carries, against the one the case expects."""
    expected_mode = exp.get("expected_result_mode")
    if expected_mode is None:
        return _ok("no result mode expected")
    actual = _norm_code(raw.get("result_mode"))
    if not actual:
        return _unverified(
            f"expected result_mode {expected_mode}, but the response carries no result_mode"
        )
    if actual == _norm_code(expected_mode):
        return _ok(f"result_mode {actual}")
    return _fail(f"result_mode {actual} != expected {expected_mode}")


def parity_verdict(
    group: str,
    members: list[GoldenCase],
    verdicts: dict[str, ComplianceVerdict],
) -> ComplianceVerdict:
    """Did the same request in RU and EN get the same KIND of answer?"""
    case_id = f"parity:{group}"
    missing = [c.id for c in members if c.id not in verdicts]
    if missing:
        return ComplianceVerdict(
            case_id=case_id,
            passed=False,
            reason=CHECK_PARITY,
            detail=f"no verdict for {missing} — the group was not fully run",
        )
    parts: dict[str, tuple] = {}
    for case in sorted(members, key=lambda c: c.locale):
        verdict = verdicts[case.id]
        missing_codes = set(
            (verdict.checks.get(CHECK_MISSING_MANDATORY_CATEGORY) or {}).get("missing") or []
        )
        parts[case.locale] = (
            verdict.status,
            tuple(sorted(verdict.failed_checks)),
            tuple(
                sorted(
                    f"{code}={'present' if code not in missing_codes else 'MISSING'}"
                    for code in case.expectations["must_contain_categories"]
                )
            ),
        )
    distinct = set(parts.values())
    ok = len(distinct) == 1

    def render(locale: str) -> str:
        status, failed, mandatory = parts[locale]
        return (
            f"{locale}: status={status}, failed={', '.join(failed) or 'none'}, "
            f"mandatory={', '.join(mandatory) or 'n/a'}"
        )

    detail = "; ".join(render(loc) for loc in sorted(parts))
    status = next(iter(parts.values()))[0]
    return ComplianceVerdict(
        case_id=case_id,
        passed=ok,
        reason="ok" if ok else CHECK_PARITY,
        detail=detail if ok else f"RU/EN parity mismatch — {detail}",
        status=status,
        status_source="parity",
        checks={CHECK_PARITY: _ok(detail) if ok else _fail(detail)},
    )


def compliance_summary(
    cases: list[GoldenCase],
    verdicts: dict[str, ComplianceVerdict],
    parity: list[ComplianceVerdict] | None = None,
) -> dict[str, Any]:
    """Per-case PASS/FAIL plus the aggregate rate over cases AND parity groups."""
    parity = parity or []
    case_verdicts = [verdicts[c.id] for c in cases if c.id in verdicts]
    unrun = [c.id for c in cases if c.id not in verdicts]
    n_passed = sum(1 for v in case_verdicts if v.passed)
    n_parity_passed = sum(1 for v in parity if v.passed)
    units = len(case_verdicts) + len(parity)
    passed_units = n_passed + n_parity_passed
    by_reason: dict[str, int] = {}
    for verdict in [*case_verdicts, *parity]:
        if not verdict.passed:
            by_reason[verdict.reason] = by_reason.get(verdict.reason, 0) + 1
    unverified = sorted(
        {f"{v.case_id}:{code}" for v in case_verdicts for code in v.unverified_checks}
    )
    return {
        "n_cases": len(case_verdicts),
        "n_cases_passed": n_passed,
        "n_cases_failed": len(case_verdicts) - n_passed,
        "n_parity_groups": len(parity),
        "n_parity_passed": n_parity_passed,
        "n_units": units,
        "n_units_passed": passed_units,
        "compliance_rate": (passed_units / units) if units else None,
        "case_pass_rate": (n_passed / len(case_verdicts)) if case_verdicts else None,
        "failures_by_reason": dict(sorted(by_reason.items())),
        "unverified_checks": unverified,
        "cases_not_run": unrun,
        "cases": [v.as_dict() for v in case_verdicts],
        "parity": [v.as_dict() for v in parity],
    }


def build_golden_request(case: GoldenCase) -> dict[str, Any]:
    """The exact body one golden case sends: the query plus its explicit filters."""
    filters = case.filters
    payload: dict[str, Any] = {
        "query": case.query,
        "locale": case.locale,
        "party_children": filters.get("party_children"),
        "hard_services": list(filters.get("hard_services") or []),
        "interests": list(filters.get("interests") or []),
        "avoid": list(filters.get("avoid") or []),
        "time_budget_minutes": filters.get("time_budget_minutes"),
        "result_mode": filters.get("result_mode") or "route",
    }
    origin = filters.get("origin")
    if origin:
        payload["origin"] = {"lat": origin["lat"], "lon": origin["lon"]}
    profile = filters.get("profile")
    if profile:
        payload["profile"] = profile
    moment = filters.get("at")
    if moment:
        payload["at"] = moment
    return payload

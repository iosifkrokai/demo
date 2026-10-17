"""Loading and validating the route, curated and compliance cases."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core import constants as agent_constants
from core.hours import parse_moment
from core.paths import ITINERARIES_JSON
from db.seed import datasets as ds_mod, itineraries as itin_mod
from quality.bench.config import (
    _PARTY_CHILDREN_MAX,
    _QUERY_MAX_CHARS,
    _QUERY_MIN_CHARS,
    BENCH_CURATED,
    BENCH_GOLDEN,
    BENCH_ROUTES,
    CANONICAL_CODES_NORM,
    REQUIREMENT_KINDS,
    REQUIREMENT_STATUSES,
    STATUSES,
    _is_num,
)
from quality.bench.models import GoldenCase, GoldenRoute, GoldenStop
from routing.valhalla.constants import VALHALLA_COSTINGS


def load_golden_routes(routes_dir: Path) -> list[GoldenRoute]:
    """Load all *.json files from routes_dir as GoldenRoute objects."""
    routes: list[GoldenRoute] = []
    for p in sorted(routes_dir.glob("*.json")):
        with p.open(encoding="utf-8") as fh:
            data = json.load(fh)
        stops = [
            GoldenStop(
                name=s["name"],
                lat=s["lat"],
                lon=s["lon"],
                visit_minutes=s.get("visit_minutes"),
                grade=s.get("grade"),
            )
            for s in data.get("stops", [])
        ]
        routes.append(
            GoldenRoute(
                name=data["name"],
                source=data.get("source", ""),
                query_ru=data["query_ru"],
                budget_minutes=data["budget_minutes"],
                stops=stops,
                est_walk_minutes=data.get("est_walk_minutes"),
                profile=data.get("profile"),
                path=p,
                case=p.stem,
            )
        )
    return routes


CURATED_REQUIRED_KEYS = frozenset({"id", "query_ru", "budget_minutes"})
CURATED_OPTIONAL_KEYS = frozenset({"est_walk_minutes", "note"})


def _check_curated_case(data: Any, path: Path) -> list[str]:
    """A sidecar carries the query and the budget, and nothing the data already holds."""
    where = str(path)
    if not isinstance(data, dict):
        return [f"{where}: a curated case must be a JSON object"]
    errs: list[str] = []
    for key in sorted(set(data) - CURATED_REQUIRED_KEYS - CURATED_OPTIONAL_KEYS):
        errs.append(f"{where}: unknown key {key!r}")
    for key in sorted(CURATED_REQUIRED_KEYS - set(data)):
        errs.append(f"{where}: missing key {key!r}")
    case_id = data.get("id")
    if "id" in data and (not isinstance(case_id, str) or case_id != path.stem):
        errs.append(f"{where}: id must be {path.stem!r}, not {case_id!r}")
    query = data.get("query_ru")
    if "query_ru" in data and (not isinstance(query, str) or not query.strip()):
        errs.append(f"{where}: query_ru must be a non-empty string")
    budget = data.get("budget_minutes")
    if "budget_minutes" in data and (
        not _is_plain_int(budget, low=1) or budget > int(agent_constants.MAX_BUDGET_MIN)
    ):
        errs.append(
            f"{where}: budget_minutes must be an int in 1..{int(agent_constants.MAX_BUDGET_MIN)}"
        )
    walk = data.get("est_walk_minutes")
    if walk is not None and not _is_plain_int(walk, low=0):
        errs.append(f"{where}: est_walk_minutes must be null or an int >= 0")
    note = data.get("note")
    if note is not None and (not isinstance(note, str) or not note.strip()):
        errs.append(f"{where}: note must be a non-empty string when present")
    return errs


def load_curated_routes(
    curated_dir: Path = BENCH_CURATED, itineraries_path: Path | None = None
) -> list[GoldenRoute]:
    """The ready-made routes as reference walks.

    The stops are read from `db/data/itineraries.json` and resolved against the
    datasets the seed projects — never copied into the case — so a walk and the
    case that measures it cannot drift apart. A sidecar supplies only what the
    data does not hold: the query words and the budget. Unknown keys are refused,
    the way a compliance case refuses them, so a sidecar cannot carry a field
    nothing reads.
    """
    sidecars = sorted(curated_dir.glob("*.json"))
    if not sidecars:
        return []
    source = itineraries_path or ITINERARIES_JSON
    walks = {walk["id"]: walk for walk in itin_mod.load_itineraries(source)}
    collected = ds_mod.collect_records(ds_mod.default_datasets())
    kept, _shadowed, _stale = ds_mod.collapse(collected["records"])
    places = {record["source_url"]: record for record in kept}

    routes: list[GoldenRoute] = []
    for path in sidecars:
        data = json.loads(path.read_text(encoding="utf-8"))
        if errs := _check_curated_case(data, path):
            raise ValueError("; ".join(errs))
        walk = walks.get(str(data.get("id")))
        if walk is None:
            raise ValueError(f"{path}: no itinerary {data.get('id')!r} in {source.name}")
        unresolved = [key for key in walk["stops"] if key not in places]
        if unresolved:
            raise ValueError(f"{path}: a stop names a source_url no dataset holds: {unresolved}")
        stops = [
            GoldenStop(
                name=places[key]["name"],
                lat=places[key]["lat"],
                lon=places[key]["lon"],
                visit_minutes=places[key].get("visit_minutes"),
            )
            for key in walk["stops"]
        ]
        routes.append(
            GoldenRoute(
                name=walk["title"],
                source=f"{source.name}#{walk['id']}",
                query_ru=str(data["query_ru"]),
                budget_minutes=int(data["budget_minutes"]),
                stops=stops,
                est_walk_minutes=data.get("est_walk_minutes"),
                profile=walk.get("transport"),
                origin=(stops[0].lat, stops[0].lon) if stops else None,
                fingerprint="\n".join(
                    f"{key}|{p.lat:.6f}|{p.lon:.6f}|{p.name}"
                    for key, p in zip(walk["stops"], stops, strict=True)
                ),
                path=path,
                case=walk["id"],
            )
        )
    return routes


def load_golden_map() -> dict[str, GoldenRoute]:
    """Every reference walk this tree knows: the benchmark's, and the ready-made ones."""
    routes = [*load_golden_routes(BENCH_ROUTES), *load_curated_routes(BENCH_CURATED)]
    return {r.case: r for r in routes}


GOLDEN_REQUIRED_TOP = frozenset({"id", "locale", "query", "filters", "expectations"})
GOLDEN_OPTIONAL_TOP = frozenset({"parity_group"})
GOLDEN_OPTIONAL_FILTER_KEYS = frozenset({"profile", "at"})
GOLDEN_FILTER_KEYS = frozenset(
    {
        "party_children",
        "hard_services",
        "interests",
        "avoid",
        "time_budget_minutes",
        "origin",
        "result_mode",
    }
)
GOLDEN_ALL_FILTER_KEYS = GOLDEN_FILTER_KEYS | GOLDEN_OPTIONAL_FILTER_KEYS
GOLDEN_REQUIRED_EXPECTATIONS = frozenset(
    {
        "must_contain_categories",
        "must_not_contain_categories",
        "expected_status",
        "max_total_minutes",
        "in_region",
    }
)
GOLDEN_OPTIONAL_EXPECTATIONS = frozenset(
    {
        "must_contain_names",
        "must_satisfy_requirement_kinds",
        "requirement_status",
        "expected_result_mode",
        "allow_empty",
        "min_places",
        "status_note",
    }
)
GOLDEN_EXPECTATION_KEYS = GOLDEN_REQUIRED_EXPECTATIONS | GOLDEN_OPTIONAL_EXPECTATIONS

LOCALES = ("ru", "en")
RESULT_MODES = ("route", "catalogue")
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
ORIGIN_BOUNDS = {"lat": (44.0, 62.0), "lon": (19.0, 42.0)}


class GoldenCaseError(ValueError):
    """A golden case that does not satisfy the documented schema."""


def _norm_code(value) -> str:
    """Canonical-code normalisation: lowercase, trimmed, ё folded to е."""
    return str(value or "").strip().lower().replace("ё", "е")


def _validate_code_list(name: str, values, errs: list[str]) -> None:
    if not isinstance(values, list):
        errs.append(f"{name}: must be a list of category codes")
        return
    for value in values:
        if not isinstance(value, str) or not value.strip():
            errs.append(f"{name}: entries must be non-empty strings")
        elif _norm_code(value) not in CANONICAL_CODES_NORM:
            errs.append(
                f"{name}: {value!r} is not a canonical category code "
                f"(see agent/constants.CATEGORIES)"
            )


def validate_golden_case(data, path: Path | None = None) -> list[str]:
    """Every way one golden case violates the schema, as strings. [] == valid."""
    where = path.name if path is not None else "<memory>"
    if not isinstance(data, dict):
        return [f"{where}: top level must be an object"]
    return (
        _golden_header(data, where, path)
        + _golden_filters(data, where)
        + _golden_expectations(data, where)
    )


def _golden_header(data: dict[str, Any], where: str, path: Path | None) -> list[str]:
    """The keys, the id, the locale, the query and the parity group."""
    errs: list[str] = []
    for key in sorted(set(data) - GOLDEN_REQUIRED_TOP - GOLDEN_OPTIONAL_TOP):
        errs.append(f"{where}: unknown top-level key {key!r}")
    for key in sorted(GOLDEN_REQUIRED_TOP - set(data)):
        errs.append(f"{where}: missing required key {key!r}")

    case_id = data.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        errs.append(f"{where}: id must be a non-empty string")
    elif path is not None and case_id != path.stem:
        errs.append(f"{where}: id {case_id!r} must equal the file name {path.stem!r}")

    locale = data.get("locale")
    if locale not in LOCALES:
        errs.append(f"{where}: locale must be one of {list(LOCALES)}, got {locale!r}")

    errs += _query_errors(data.get("query"), locale, where)

    group = data.get("parity_group")
    if group is not None and (not isinstance(group, str) or not group.strip()):
        errs.append(f"{where}: parity_group must be a non-empty string when present")
    return errs


def _query_errors(query: Any, locale: Any, where: str) -> list[str]:
    """The query's length, and whether its script matches the locale it claims."""
    if not isinstance(query, str) or not (
        _QUERY_MIN_CHARS <= len(query.strip()) <= _QUERY_MAX_CHARS
    ):
        return [
            f"{where}: query must be a string of {_QUERY_MIN_CHARS}..{_QUERY_MAX_CHARS} characters"
        ]
    if locale not in LOCALES:
        return []
    has_cyrillic = bool(_CYRILLIC_RE.search(query))
    errs: list[str] = []
    if locale == "en" and has_cyrillic:
        errs.append(f"{where}: locale is 'en' but the query contains Cyrillic")
    if locale == "ru" and not has_cyrillic:
        errs.append(f"{where}: locale is 'ru' but the query contains no Cyrillic")
    return errs


def _is_plain_int(value: Any, *, low: int | None = None, high: int | None = None) -> bool:
    """A real int inside the bounds — `True` is a bool, and no bound means none here."""
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    if low is not None and value < low:
        return False
    return not (high is not None and value > high)


def _golden_filters(data: dict[str, Any], where: str) -> list[str]:
    """The filters block: its keys, the party, and the bounds on budget and origin."""
    filters = data.get("filters")
    if not isinstance(filters, dict):
        return [f"{where}: filters must be an object"]
    return (
        _filter_keys(filters, where)
        + _filter_profile(filters, where)
        + _filter_moment(filters, where)
        + _filter_overlap(filters, where)
        + _filter_budget(filters, where)
        + _filter_result_mode(filters, where)
        + _filter_origin(filters, where)
    )


def _filter_moment(filters: dict[str, Any], where: str) -> list[str]:
    """The moment the walk is planned for, as a Grodno wall clock or an instant."""
    moment = filters.get("at")
    if moment is None:
        return []
    if not isinstance(moment, str):
        return [f"{where}: filters.at must be null or an ISO moment, got {moment!r}"]
    try:
        parse_moment(moment)
    except ValueError:
        return [f"{where}: filters.at {moment!r} is not an ISO moment"]
    return []


def _filter_profile(filters: dict[str, Any], where: str) -> list[str]:
    """The transport the tourist picked. Absent means the API's own default."""
    profile = filters.get("profile")
    if profile is None or profile in VALHALLA_COSTINGS:
        return []
    return [
        (
            f"{where}: filters.profile must be null or one of "
            f"{sorted(VALHALLA_COSTINGS)}, got {profile!r}"
        )
    ]


def _filter_keys(filters: dict[str, Any], where: str) -> list[str]:
    """The keys the schema names, the party size, and the codes to look for."""
    errs: list[str] = []
    for key in sorted(set(filters) - GOLDEN_ALL_FILTER_KEYS):
        errs.append(f"{where}: unknown filters key {key!r}")
    for key in sorted(GOLDEN_FILTER_KEYS - set(filters)):
        errs.append(f"{where}: missing filters key {key!r}")
    children = filters.get("party_children")
    if children is not None and not _is_plain_int(children, low=0, high=_PARTY_CHILDREN_MAX):
        errs.append(f"{where}: party_children must be null or an int in 0..{_PARTY_CHILDREN_MAX}")
    for key in ("hard_services", "interests", "avoid"):
        if key in filters:
            _validate_code_list(f"{where}: filters.{key}", filters[key], errs)
    return errs


def _filter_overlap(filters: dict[str, Any], where: str) -> list[str]:
    """A code cannot be both mandatory and forbidden, nor both wanted and forbidden."""
    hard = {_norm_code(c) for c in filters.get("hard_services") or []}
    avoid = {_norm_code(c) for c in filters.get("avoid") or []}
    interests = {_norm_code(c) for c in filters.get("interests") or []}
    errs: list[str] = []
    if hard & avoid:
        errs.append(
            f"{where}: filters.hard_services and filters.avoid overlap "
            f"({sorted(hard & avoid)}) — a code cannot be mandatory and forbidden"
        )
    if interests & avoid:
        errs.append(
            f"{where}: filters.interests and filters.avoid overlap ({sorted(interests & avoid)})"
        )
    return errs


def _filter_budget(filters: dict[str, Any], where: str) -> list[str]:
    """The time budget, and that it stays inside what the API will accept."""
    budget = filters.get("time_budget_minutes")
    if budget is None:
        return []
    if not _is_plain_int(budget, low=0):
        return [f"{where}: filters.time_budget_minutes must be null or an int >= 0"]
    if budget > int(agent_constants.MAX_BUDGET_MIN):
        return [
            (
                f"{where}: filters.time_budget_minutes {budget} exceeds the API "
                f"maximum {agent_constants.MAX_BUDGET_MIN}"
            )
        ]
    return []


def _filter_result_mode(filters: dict[str, Any], where: str) -> list[str]:
    """The result mode the case asks for."""
    mode = filters.get("result_mode")
    if mode in RESULT_MODES:
        return []
    return [f"{where}: filters.result_mode must be one of {list(RESULT_MODES)}, got {mode!r}"]


def _filter_origin(filters: dict[str, Any], where: str) -> list[str]:
    """The optional starting point, inside the box the API accepts."""
    origin = filters.get("origin")
    if origin is None:
        return []
    if not isinstance(origin, dict) or set(origin) != {"lat", "lon"}:
        return [f"{where}: filters.origin must be null or {{lat, lon}}"]
    errs: list[str] = []
    for axis, (lo, hi) in ORIGIN_BOUNDS.items():
        value = origin.get(axis)
        if not _is_num(value) or not (lo <= value <= hi):
            errs.append(
                f"{where}: filters.origin.{axis} must be a number in {lo}..{hi}, got {value!r}"
            )
    return errs


def _golden_expectations(data: dict[str, Any], where: str) -> list[str]:
    """The expectations block: its keys, the lists, the bounds, and the flags."""
    exp = data.get("expectations")
    if not isinstance(exp, dict):
        return [f"{where}: expectations must be an object"]
    return (
        _expectation_keys(exp, where)
        + _expectation_codes(exp, where)
        + _expectation_status(exp, where)
        + _expectation_result_mode(exp, where)
        + _expectation_bounds(exp, where, data.get("filters"))
        + _expectation_flags(exp, where)
    )


def _expectation_keys(exp: dict[str, Any], where: str) -> list[str]:
    """The keys the schema names, and none missing."""
    errs: list[str] = []
    for key in sorted(set(exp) - GOLDEN_EXPECTATION_KEYS):
        errs.append(f"{where}: unknown expectations key {key!r}")
    for key in sorted(GOLDEN_REQUIRED_EXPECTATIONS - set(exp)):
        errs.append(f"{where}: missing expectations key {key!r}")
    return errs


def _expectation_codes(exp: dict[str, Any], where: str) -> list[str]:
    """The categories and the names the case says must (or must not) appear."""
    errs: list[str] = []
    for key in ("must_contain_categories", "must_not_contain_categories"):
        if key in exp:
            _validate_code_list(f"{where}: expectations.{key}", exp[key], errs)
    names = exp.get("must_contain_names")
    if names is not None and not _name_list_ok(names):
        errs.append(
            f"{where}: expectations.must_contain_names must be a non-empty "
            "list of non-empty strings when present"
        )
    wanted = exp.get("requirement_status")
    if wanted is not None:
        if not isinstance(wanted, dict) or not wanted:
            errs.append(f"{where}: expectations.requirement_status must be a non-empty object")
        else:
            errs += [
                f"{where}: expectations.requirement_status {kind!r} is not one of "
                f"{sorted(REQUIREMENT_KINDS)}"
                for kind in wanted
                if kind not in REQUIREMENT_KINDS
            ]
            errs += [
                f"{where}: expectations.requirement_status[{kind!r}] must be one of "
                f"{sorted(REQUIREMENT_STATUSES)}, not {status!r}"
                for kind, status in wanted.items()
                if status not in REQUIREMENT_STATUSES
            ]
    kinds = exp.get("must_satisfy_requirement_kinds")
    if kinds is not None:
        if not _name_list_ok(kinds):
            errs.append(
                f"{where}: expectations.must_satisfy_requirement_kinds must be a "
                "non-empty list of non-empty strings when present"
            )
        else:
            errs += [
                f"{where}: expectations.must_satisfy_requirement_kinds {kind!r} is not "
                f"one of {sorted(REQUIREMENT_KINDS)}"
                for kind in kinds
                if kind not in REQUIREMENT_KINDS
            ]
    return errs


def _name_list_ok(names: Any) -> bool:
    """A non-empty list of non-empty strings."""
    if not isinstance(names, list) or not names:
        return False
    return all(isinstance(n, str) and n.strip() for n in names)


def _expectation_status(exp: dict[str, Any], where: str) -> list[str]:
    """The plan statuses the case will accept."""
    statuses = exp.get("expected_status")
    if not isinstance(statuses, list) or not statuses:
        return [f"{where}: expectations.expected_status must be a non-empty list"]
    return [
        f"{where}: expectations.expected_status {status!r} is not one of {sorted(STATUSES)}"
        for status in statuses
        if status not in STATUSES
    ]


def _expectation_result_mode(exp: dict[str, Any], where: str) -> list[str]:
    """The result mode the case expects, when it says."""
    mode = exp.get("expected_result_mode")
    if mode is None or mode in RESULT_MODES:
        return []
    return [f"{where}: expectations.expected_result_mode must be {RESULT_MODES}"]


def _expectation_bounds(exp: dict[str, Any], where: str, filters: Any) -> list[str]:
    """The two numeric bounds: the total-minutes cap and the minimum stop count."""
    errs: list[str] = []
    cap = exp.get("max_total_minutes")
    if cap is not None and not _is_plain_int(cap, low=1):
        errs.append(f"{where}: expectations.max_total_minutes must be null or int > 0")
    elif (
        cap is not None
        and isinstance(filters, dict)
        and isinstance(filters.get("time_budget_minutes"), int)
        and cap > filters["time_budget_minutes"]
    ):
        errs.append(
            f"{where}: expectations.max_total_minutes {cap} exceeds the stated "
            f"budget {filters['time_budget_minutes']} — the cap would never bind"
        )
    min_places = exp.get("min_places")
    if min_places is not None and not _is_plain_int(min_places, low=0):
        errs.append(f"{where}: expectations.min_places must be null or an int >= 0")
    elif min_places is not None and not exp.get("allow_empty") and min_places == 0:
        errs.append(
            f"{where}: expectations.min_places 0 with allow_empty false — an "
            "empty plan would pass; say allow_empty: true instead"
        )
    return errs


def _expectation_flags(exp: dict[str, Any], where: str) -> list[str]:
    """The three booleans, and the free-text note."""
    errs: list[str] = []
    if not isinstance(exp.get("in_region"), bool):
        errs.append(f"{where}: expectations.in_region must be a boolean")
    if "allow_empty" in exp and not isinstance(exp["allow_empty"], bool):
        errs.append(f"{where}: expectations.allow_empty must be a boolean")
    if exp.get("status_note") is not None and not isinstance(exp["status_note"], str):
        errs.append(f"{where}: expectations.status_note must be a string")
    return errs


def golden_case_from_dict(data: dict[str, Any], path: Path | None = None) -> GoldenCase:
    """Build a GoldenCase from validated data (validate first, or it raises)."""
    errs = validate_golden_case(data, path)
    if errs:
        raise GoldenCaseError("; ".join(errs))
    return GoldenCase(
        id=data["id"],
        locale=data["locale"],
        query=data["query"],
        filters=dict(data["filters"]),
        expectations=dict(data["expectations"]),
        parity_group=data.get("parity_group"),
        path=path,
    )


def load_golden_cases(directory: Path | None = None) -> list[GoldenCase]:
    """Load and validate every case in quality/cases/compliance (never silently skips)."""
    directory = Path(directory) if directory is not None else BENCH_GOLDEN
    files = sorted(directory.glob("*.json"))
    if not files:
        raise GoldenCaseError(f"no golden cases in {directory}")
    cases: list[GoldenCase] = []
    problems: list[str] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name}: invalid JSON ({exc})")
            continue
        errs = validate_golden_case(data, path)
        if errs:
            problems.extend(errs)
        else:
            cases.append(golden_case_from_dict(data, path))
    groups = validate_parity_groups(cases)
    problems.extend(groups)
    if problems:
        raise GoldenCaseError(
            f"{len(problems)} golden-schema violation(s):" + "".join(f"\n  - {p}" for p in problems)
        )
    return cases


def golden_by_id(cases: list[GoldenCase]) -> dict[str, GoldenCase]:
    return {c.id: c for c in cases}


def _parity_signature(case: GoldenCase) -> tuple:
    """What must be identical across a parity group: filters and conditions."""
    exp = {k: v for k, v in case.expectations.items() if k != "status_note"}
    return (
        json.dumps(case.filters, sort_keys=True, ensure_ascii=False),
        json.dumps(exp, sort_keys=True, ensure_ascii=False),
    )


def validate_parity_groups(cases: list[GoldenCase]) -> list[str]:
    """A parity group must really be ONE request expressed in RU and EN."""
    errs: list[str] = []
    groups: dict[str, list[GoldenCase]] = {}
    for case in cases:
        if case.parity_group:
            groups.setdefault(case.parity_group, []).append(case)
    for name, members in sorted(groups.items()):
        locales = sorted(c.locale for c in members)
        if locales != sorted(LOCALES):
            errs.append(
                f"parity_group {name!r}: must hold exactly one case per locale "
                f"({list(LOCALES)}), got {locales}"
            )
        signatures = {_parity_signature(c) for c in members}
        if len(signatures) > 1:
            detail = "; ".join(
                f"{c.locale}={_parity_signature(c)[0]}|{_parity_signature(c)[1]}"
                for c in sorted(members, key=lambda c: c.locale)
            )
            errs.append(
                f"parity_group {name!r}: the filters/expectations differ between "
                f"locales — this is not the same request ({detail})"
            )
        queries = [c.query for c in members]
        if len(set(queries)) != len(queries):
            errs.append(f"parity_group {name!r}: two locales carry the same query text")
    return errs


def parity_groups(cases: list[GoldenCase]) -> dict[str, list[GoldenCase]]:
    out: dict[str, list[GoldenCase]] = {}
    for case in cases:
        if case.parity_group:
            out.setdefault(case.parity_group, []).append(case)
    return out

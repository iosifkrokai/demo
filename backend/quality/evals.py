#!/usr/bin/env python
"""Stage evals for the Grodno guide: one runner, one honest report."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent import client
from agent.models import Requirement, TripRequirements
from agent.tools.places import Where, find_places
from api.models.routes import GenerateReq
from api.responses import reader_brief
from core import constants
from db.embeddings import model as embeddings_model
from db.seed.taxonomy import all_categories as _seed_categories
from db.store import services as services_mod
from db.store.areas import PostgresAreaRepository
from db.store.errors import StorageUnavailableError
from db.store.places import PostgresPlaceRepository
from db.store.registry import Repositories
from quality.runner import BENCH_ROUTES, STAGE1_PROXY_RADIUS_M, haversine_km, load_golden_routes
from routing.models import CategoryFacts
from routing.verify import ServiceAlongEvidence, verify

QUALITY_FACTS = CategoryFacts.from_rows(_seed_categories())
_SEED_ROLES = {cat.code: cat.role for cat in _seed_categories()}


_TIE_M = 5.0


def _role(code: str) -> str | None:
    """The role of a canonical code, or None when it is not one."""
    return _SEED_ROLES.get(code)


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = Path(__file__).resolve().parent / "cases"

WEIGHTS: dict[str, float] = {
    "verdicts": 0.25,
    "services": 0.20,
    "interpretation": 0.30,
    "plan": 0.25,
}


class Case:
    """One eval case: the input, the expectations, and why it exists."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.id: str = raw["id"]
        self.why: str = raw["why"]


def _load(name: str) -> list[Case]:
    path = CASES / f"{name}.jsonl"
    out = []
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            out.append(Case(json.loads(line)))
        except (json.JSONDecodeError, KeyError) as exc:
            raise SystemExit(f"{path}:{lineno}: {exc}") from exc
    return out


def run_verdicts() -> dict[str, Any]:
    """The deterministic verifier against the contract it must keep."""

    checks: list[dict[str, Any]] = []
    for case in _load("verdicts"):
        raw = case.raw
        reqs = TripRequirements(requirements=[Requirement(**dict(r)) for r in raw["requirements"]])
        evidence = None
        if raw.get("evidence") is not None:
            evidence = ServiceAlongEvidence(
                measured=bool(raw["evidence"]["measured"]),
                by_code=raw["evidence"].get("by_code", {}),
            )
        try:
            result = verify(reqs, raw["plan"], raw.get("geometry"), evidence, facts=QUALITY_FACTS)
        except Exception as exc:
            checks.append(
                {
                    "case": case.id,
                    "check": "verdict",
                    "ok": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                    "why": case.why,
                }
            )
            continue

        got = [(r.status, r.reason) for r in result]
        want = [(e["status"], e["reason"]) for e in raw["expect"]]
        checks.append(
            {
                "case": case.id,
                "check": "verdict",
                "ok": got == want,
                "known_gap": bool(raw.get("known_gap")) and got != want,
                "detail": f"получено {got}, ожидалось {want}",
                "why": case.why,
            }
        )
    return {"stage": "verdicts", "checks": checks, "skipped": None}


def _off_line_and_along(
    lat: float, lon: float, line: list[tuple[float, float]]
) -> tuple[float, float]:
    """Independent reference: metres off the polyline and metres along it."""
    lat0 = sum(p[0] for p in line) / len(line)
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110574.0
    pts = [((p[1] - line[0][1]) * kx, (p[0] - line[0][0]) * ky) for p in line]
    px, py = (lon - line[0][1]) * kx, (lat - line[0][0]) * ky

    best = (float("inf"), 0.0, 0.0)
    run = 0.0
    for i in range(len(pts) - 1):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        dx, dy = bx - ax, by - ay
        seg_len = math.hypot(dx, dy)
        t = (
            0.0
            if seg_len == 0
            else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len**2))
        )
        cx, cy = ax + t * dx, ay + t * dy
        dist = math.hypot(px - cx, py - cy)
        along = run + t * seg_len
        if dist < best[0]:
            best = (dist, along, run + seg_len)
        run += seg_len
    total = run
    return best[0], (best[1] / total if total else 0.0)


def run_services() -> dict[str, Any]:
    """Does the measurement tell the truth about what lies beside the line?"""

    checks: list[dict[str, Any]] = []
    repos = _repositories()
    near_gate = 0
    try:
        service_codes = tuple(repos.taxonomy.service_codes())
        for case in _load("services_along"):
            case_checks, ties = _service_checks(repos.places, case, service_codes)
            checks.extend(case_checks)
            near_gate += ties
    except StorageUnavailableError as exc:
        return {
            "stage": "services",
            "checks": checks,
            "skipped": f"нет доступа к базе: {type(exc).__name__}: {exc}",
        }
    finally:
        repos.close()

    return {"stage": "services", "checks": checks, "skipped": None, "near_gate_points": near_gate}


def _service_checks(
    places: Any, case: Any, service_codes: tuple[str, ...]
) -> tuple[list[dict[str, Any]], int]:
    """Every check one services_along case makes of its own response.

    ``service_codes`` must be the taxonomy's own, or the store answers an empty catalogue."""
    checks: list[dict[str, Any]] = []
    raw = case.raw
    shape = raw["shape"]
    line = [(lat, lon) for lon, lat in shape["coordinates"]]
    codes = services_mod.service_codes(raw["categories"], service_codes)
    gate = float(raw.get("gate_m") or services_mod.threshold_for(raw.get("profile", "pedestrian")))

    answer = places.services_along(
        shape,
        categories=codes,
        service_category_codes=service_codes,
        profile=raw.get("profile", "pedestrian"),
        max_off_line_m=gate,
        limit=raw.get("limit", services_mod.MAX_SERVICES),
    )
    items = answer["items"]

    reference = places.with_category(codes, lat_lon_only=True)
    ref: dict[int, tuple[float, float]] = {}
    for place in reference:
        if place.id is None:
            continue
        off, along = _off_line_and_along(place.lat, place.lon, line)
        if off <= gate:
            ref[place.id] = (off, along)

    bad_role = [i["category"] for i in items if _role_of(i["category"]) != "service"]
    checks.append(
        {
            "case": case.id,
            "check": "role_only",
            "ok": not bad_role,
            "detail": f"не-услуги в ответе: {bad_role}",
            "why": case.why,
        }
    )

    alongs = [i["along_m"] for i in items]
    checks.append(
        {
            "case": case.id,
            "check": "ordered_along",
            "ok": alongs == sorted(alongs),
            "detail": f"along_m = {alongs}",
            "why": case.why,
        }
    )

    over = [i["off_line_m"] for i in items if i["off_line_m"] > gate + 1]
    checks.append(
        {
            "case": case.id,
            "check": "within_gate",
            "ok": not over,
            "detail": f"за порогом {gate} м: {over}",
            "why": case.why,
        }
    )

    cap = int(raw.get("limit", services_mod.MAX_SERVICES))
    ref_capped = dict(sorted(ref.items(), key=lambda kv: kv[1][1])[:cap])
    safe_ref = {
        pid
        for pid, (off, _) in ref_capped.items()
        if abs(off - gate) > _TIE_M and gate - off > _TIE_M
    }
    safe_got = {
        i["id"]
        for i in items
        if abs(i["off_line_m"] - gate) > _TIE_M and gate - i["off_line_m"] > _TIE_M
    }
    ties = len(ref_capped) - len(safe_ref)
    checks.append(
        {
            "case": case.id,
            "check": "cap_matches_limit",
            "ok": len(items) <= cap,
            "detail": f"в ответе {len(items)} точек при пределе {cap}",
            "why": case.why,
        }
    )
    missing = safe_ref - safe_got
    extra = safe_got - safe_ref
    checks.append(
        {
            "case": case.id,
            "check": "membership_agrees",
            "ok": not missing and not extra,
            "detail": (
                f"нет в ответе {sorted(missing)}, лишние {sorted(extra)}"
                f" (у порога не считаем: {ties})"
            ),
            "why": case.why,
        }
    )

    ref_off = {pid: off for pid, (off, _) in ref.items()}
    worst = 0.0
    allowance = 2.0
    for item in items:
        if item["id"] in ref_off:
            diff = abs(item["off_line_m"] - ref_off[item["id"]])
            allowance = max(allowance, 0.005 * ref_off[item["id"]] + 2.0)
            worst = max(worst, diff)
    checks.append(
        {
            "case": case.id,
            "check": "distance_agrees",
            "ok": worst <= allowance,
            "detail": f"максимальное расхождение {worst:.2f} м (допуск {allowance:.2f} м)",
            "why": case.why,
        }
    )

    flags_ok = (
        answer.get("detour_confirmed") is False
        and answer.get("measured") == "distance_to_line"
        and answer.get("not_measured") == "detour_walking_time"
    )
    checks.append(
        {
            "case": case.id,
            "check": "honest_flags",
            "ok": flags_ok,
            "detail": f"detour_confirmed={answer.get('detour_confirmed')}, "
            f"measured={answer.get('measured')}, "
            f"not_measured={answer.get('not_measured')}",
            "why": case.why,
        }
    )

    if len(items) >= int(raw.get("limit", services_mod.MAX_SERVICES)):
        checks.append(
            {
                "case": case.id,
                "check": "cap_is_declared",
                "ok": answer.get("capped") is True,
                "detail": f"capped={answer.get('capped')} при {len(items)} точках",
                "why": case.why,
            }
        )

    if raw.get("monotonic_with_gate"):
        cap_here = int(raw.get("limit", services_mod.MAX_SERVICES))
        wider = places.services_along(
            shape,
            categories=codes,
            service_category_codes=service_codes,
            profile=raw["profile"],
            max_off_line_m=gate * 3,
            limit=max(cap_here, cap_here * 3),
        )
        ok = len(wider["items"]) >= len(items)
        checks.append(
            {
                "case": case.id,
                "check": "wider_gate_finds_more",
                "ok": ok,
                "detail": (
                    f"{len(items)} при {gate:.0f} м → {len(wider['items'])} при {gate * 3:.0f} м"
                ),
                "why": case.why,
            }
        )
    return checks, ties


def _role_of(code: str) -> str:
    """The role of a canonical code, or "unknown" when it is not one."""
    return _role(code) or "unknown"


BASE_URL = os.environ.get("EVALS_BASE_URL", "http://localhost:8080")


def run_plan() -> dict[str, Any]:
    """What the walk is actually made of, over HTTP."""

    cases = _load("plan")
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append(
            {
                "case": case.id,
                "check": name,
                "ok": ok,
                "detail": detail,
                "why": case.why,
                "known_gap": False,
            }
        )

    for case in cases:
        raw = case.raw
        body = json.dumps(
            {
                "query": raw["query"],
                "time_budget_minutes": raw.get("budget_minutes", 120),
                "profile": raw.get("profile", "pedestrian"),
                "origin": raw.get("origin") or {"lat": 53.6789, "lon": 23.8295},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{BASE_URL}/routes/generate",
            data=body,
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:160]
            check("request_is_answered", False, f"HTTP {exc.code}: {detail}")
            continue
        except Exception as exc:
            return {
                "stage": "plan",
                "checks": [],
                "skipped": f"приложение не отвечает на {BASE_URL} ({exc})",
            }

        stops = payload.get("points") or []
        requirements = (payload.get("interpretation") or {}).get("requirements") or []

        if raw.get("expect", {}).get("stops_are_not_services", True):
            offenders = [
                f"{s.get('name')} [{s.get('category')}]"
                for s in stops
                if _role_of(s.get("category") or "") == "service"
            ]
            check(
                "stops_are_not_services",
                not offenders,
                f"услуги среди остановок: {offenders}"
                if offenders
                else f"{len(stops)} остановок, услуги среди них нет",
            )

        if raw.get("expect", {}).get("avoid_is_honoured"):
            forbidden = {
                r.get("code") for r in requirements if r.get("kind") == "avoid" and r.get("code")
            }
            verdicts = [
                (r.get("code"), r.get("status"), r.get("reason"))
                for r in requirements
                if r.get("kind") == "avoid"
            ]
            in_plan = [
                f"{s.get('name')} [{s.get('category')}]"
                for s in stops
                if (s.get("category") or "") in forbidden
            ]
            violated = [code for code, status, _reason in verdicts if status != "satisfied"]
            ok = not in_plan and not violated
            detail = (
                f"запрет в плане: {in_plan}, вердикты: {verdicts}"
                if not ok
                else f"вердикты: {verdicts}, запрещённого в плане нет"
            )
            check("avoid_is_honoured", ok, detail)

        check(
            "request_is_answered",
            True,
            f"статус {payload.get('status')}, остановок {len(stops)}",
        )

    return {"stage": "plan", "checks": checks, "base_url": BASE_URL}


AREA_QUERY = "достопримечательности"


def run_retrieval() -> dict[str, Any]:
    """Does the pool reach the stops the data holds, and stay inside the area it was asked for?"""

    if not embeddings_model.is_available():
        return {
            "stage": "retrieval",
            "checks": [],
            "skipped": "нет локальной модели эмбеддингов — векторный поиск нечем измерить",
        }

    checks: list[dict[str, Any]] = []
    repos = _repositories()
    try:
        checks.extend(_area_checks(repos))
        pool_checks, recall = _pool_checks(repos)
        checks.extend(pool_checks)
    except StorageUnavailableError as exc:
        return {
            "stage": "retrieval",
            "checks": checks,
            "skipped": f"нет доступа к базе: {type(exc).__name__}: {exc}",
        }
    finally:
        repos.close()

    return {"stage": "retrieval", "checks": checks, "skipped": None, "pool_recall": recall}


def _pool(query: str, repos: Any, area: str | None = None) -> list[dict[str, Any]]:
    """The rows one search hands the agent — the pool it chooses the stops from."""
    where = Where(area=area) if area else None
    found = find_places(query, limit=constants.MAX_PLACES_PER_FIND, repos=repos, where=where)
    return list(found.get("results") or [])


def _nearest_km(stop: Any, rows: list[dict[str, Any]]) -> float | None:
    """How far the nearest returned row stands from the stop; None when nothing came back."""
    if not rows:
        return None
    return min(haversine_km(stop.lat, stop.lon, row["lat"], row["lon"]) for row in rows)


def _reaches(stop: Any, rows: list[dict[str, Any]]) -> bool:
    """Whether anything returned stands within the benchmark's own 250 m of the stop."""
    near = _nearest_km(stop, rows)
    return near is not None and near * 1000.0 <= STAGE1_PROXY_RADIUS_M


def _covered(stops: list[Any], rows: list[dict[str, Any]]) -> int:
    """How many of these stops the returned pool reaches."""
    return sum(1 for stop in stops if _reaches(stop, rows))


def _held(repos: Any, name: str) -> Any | None:
    """The row the corpus keeps under this exact name, or None.

    The case coordinates are the walk author's; only an exact name identifies a stop."""
    wanted = name.strip().lower()
    for place in repos.places.keyword_search(name, limit=20):
        if place.name.strip().lower() == wanted:
            return place
    return None


def _area_checks(repos: Any) -> list[dict[str, Any]]:
    """Every territory that has a boundary: a search scoped to it must stay inside it."""
    checks: list[dict[str, Any]] = []
    for area in repos.areas.list_all():
        if not repos.areas.has_boundary(area.code):
            continue
        ids = [row["id"] for row in _pool(AREA_QUERY, repos, area.code)]
        outside = repos.places.outside_area(ids, area.code)
        checks.append(
            {
                "case": area.code,
                "check": "scoped_search_stays_inside_the_area",
                "ok": bool(ids) and not outside,
                "detail": f"{area.name_ru}: вернулось {len(ids)} точек, вне границы {len(outside)}",
                "why": (
                    "Площадка в запросе обязана ограничивать поиск: без границы агент думает, "
                    "что ищет по этому кварталу, а получает точки по всей области."
                ),
            }
        )
    return checks


def _pool_checks(repos: Any) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """The reference walks: does the pool reach their stops, and asked which way?"""
    checks: list[dict[str, Any]] = []
    covered = {"whole": 0, "per_object": 0, "stops": 0}
    for route in load_golden_routes(BENCH_ROUTES):
        stops = list(route.stops)
        one = _pool(route.query_ru, repos)
        per_object = [row for stop in stops for row in _pool(stop.name, repos)]
        names = [row["name"] for row in one + per_object]
        covered["whole"] += _covered(stops, one)
        covered["per_object"] += _covered(stops, per_object)
        covered["stops"] += len(stops)
        checks.append(
            {
                "case": route.case,
                "check": "per_object_query_is_not_worse",
                "ok": _covered(stops, per_object) >= _covered(stops, one),
                "detail": (
                    f"одним запросом {_covered(stops, one)}/{len(stops)}, "
                    f"по объектам {_covered(stops, per_object)}/{len(stops)}; "
                    f"повторов имён в пуле {len(names) - len(set(names))} из {len(names)}"
                ),
                "why": (
                    "Длинная фраза усредняется в вектор, который не находит ничего: названные "
                    "объекты надо искать по одному и их же словами."
                ),
            }
        )
        for stop in stops:
            rows = _pool(stop.name, repos)
            held = _held(repos, stop.name)
            mine = any(row["id"] == held.id for row in rows) if held is not None else False
            near = _nearest_km(held, rows) if held is not None else None
            if held is None:
                detail = f"вернулось {len(rows)}; места с таким именем в данных нет"
            elif mine:
                detail = f"вернулось {len(rows)}, наша строка #{held.id} в пуле"
            elif near is not None:
                detail = f"вернулось {len(rows)}, ближайшая к нашей строке в {near * 1000:.0f} м"
            else:
                detail = f"вернулось {len(rows)}, ни одной точки рядом с нашей строкой"
            checks.append(
                {
                    "case": f"{route.case}/{stop.name}",
                    "check": "stop_is_reachable_by_its_name",
                    "ok": held is not None and _reaches(held, rows),
                    "known_gap": held is None,
                    "detail": detail,
                    "why": (
                        "Место, которое есть в данных, обязано попадать в пул по своему имени — "
                        "само или строка-двойник того же объекта в 250 м от него. Стоп, "
                        "которого в данных нет, — пробел в кейсах, а не в поиске."
                    ),
                }
            )
    total = covered["stops"] or 1
    recall = {
        "whole": covered["whole"] / total,
        "per_object": covered["per_object"] / total,
        "stops": covered["stops"],
    }
    return checks, recall


def _repositories() -> Repositories:
    """The repositories a live stage reads through, skipping when there is none."""
    return Repositories(places=PostgresPlaceRepository(), areas=PostgresAreaRepository())


def run_interpretation() -> dict[str, Any]:
    """Did the request get read as it was meant?"""
    repos = _repositories()

    checks: list[dict[str, Any]] = []
    sources: dict[str, int] = {}
    for case in _load("interpretation"):
        raw = case.raw
        req = GenerateReq(query=raw["query"], **(raw.get("filters") or {}))
        try:
            outcome = client.plan_with_agent(raw["query"], reader_brief(req), repos=repos)
            got = outcome.requirements if outcome is not None else None
        except Exception as exc:
            checks.append(
                {
                    "case": case.id,
                    "check": "reads_request",
                    "ok": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                    "why": case.why,
                }
            )
            continue

        if got is None:
            checks.append(
                {
                    "case": case.id,
                    "check": "reads_request",
                    "ok": False,
                    "detail": "агент не вернул чтения",
                    "why": case.why,
                }
            )
            continue

        source = str(getattr(got, "source", "?"))
        sources[source] = sources.get(source, 0) + 1
        reading = sorted((r.kind, r.code or r.name or "-", r.strength) for r in got.requirements)

        ok = False
        for acceptable in raw["acceptable"]:
            if all(
                any(
                    k == a["kind"]
                    and (a.get("code") is None or c == a["code"])
                    and s == a["strength"]
                    for k, c, s in reading
                )
                for a in acceptable
            ):
                ok = True
                break

        checks.append(
            {
                "case": case.id,
                "check": "reads_request",
                "ok": ok,
                "detail": f"источник={source}, прочитано {reading}",
                "why": case.why,
            }
        )

        invented = [r.code for r in got.requirements if r.code and not _code_exists(r.code)]
        checks.append(
            {
                "case": case.id,
                "check": "no_invented_codes",
                "ok": not invented,
                "detail": f"выдуманные коды: {invented}",
                "why": case.why,
            }
        )

        forbid = raw.get("forbid") or []
        if forbid:
            broke = _forbidden(reading, forbid)
            checks.append(
                {
                    "case": case.id,
                    "check": "no_forbidden_reading",
                    "ok": not broke,
                    "detail": f"прочитано запрещённое: {broke}",
                    "why": case.why,
                }
            )

        unplaceable = [
            (r.name or r.code or "-")
            for r in got.requirements
            if r.kind == "must_visit" and r.place_id is None
        ]
        checks.append(
            {
                "case": case.id,
                "check": "must_visit_is_placeable",
                "ok": not unplaceable,
                "detail": f"обязательные места, не привязанные к месту: {unplaceable}",
                "why": case.why,
            }
        )

    for check in checks:
        if not check["ok"] and any(
            c.raw.get("known_gap") and c.id == check["case"] for c in _load("interpretation")
        ):
            check["known_gap"] = True

    return {
        "stage": "interpretation",
        "checks": checks,
        "skipped": None,
        "sources": sources,
    }


def _forbidden(reading: list[tuple[str, str, str]], forbid: list[dict[str, Any]]) -> list[str]:
    """The reading the case rules out — what the product must not take the words to mean."""
    out = []
    for kind, code, strength in reading:
        for spec in forbid:
            same_code = spec.get("code") is None or code == spec["code"]
            if kind == spec["kind"] and strength == spec["strength"] and same_code:
                out.append(f"{kind}/{code}/{strength}")
                break
    return out


def _code_exists(code: str) -> bool:
    """True when the code is a canonical taxonomy code."""
    return _role(code) is not None


STAGES: dict[str, Callable[[], dict[str, Any]]] = {
    "verdicts": run_verdicts,
    "services": run_services,
    "retrieval": run_retrieval,
    "interpretation": run_interpretation,
    "plan": run_plan,
}


def _rate(checks: list[dict[str, Any]]) -> tuple[int, int]:
    """Pass rate over the checks that count; documented gaps are excluded."""
    counted = [c for c in checks if not c.get("known_gap")]
    return sum(1 for c in counted if c["ok"]), len(counted)


def _stage_rows(res: dict[str, Any]) -> tuple[list[str], tuple[int, int]]:
    """One stage's rows in the table, with the counted (passed, total) they came from."""
    passed, total = _rate(res["checks"])
    stage = res["stage"]
    if res.get("skipped"):
        return [f"{stage:<22} {'—':>6} {'—':>8}   пропущен: {res['skipped']}"], (passed, total)
    if not total:
        return [f"{stage:<22} {total:>6} {passed:>8}   {'—':>5}"], (passed, total)
    rows = [f"{stage:<22} {total:>6} {passed:>8}   {passed / total:>5.0%}"]
    if res.get("sources"):
        rows.append(f"{'':<22} источник разбора: {res['sources']}")
    if res.get("near_gate_points"):
        rows.append(
            f"{'':<22} точек у самого порога (не считаем расхождением): {res['near_gate_points']}"
        )
    recall = res.get("pool_recall")
    if recall:
        rows.append(
            f"{'':<22} резерв пула (250 м, {recall['stops']} остановок): "
            f"одним запросом {recall['whole']:.0%}, по объектам {recall['per_object']:.0%}"
        )
    return rows, (passed, total)


def _counted_weights(counted: list[str]) -> str:
    """The legend of the weights the total was actually computed over."""
    return "   веса учтённых участков: " + ", ".join(
        f"{stage} {WEIGHTS[stage]:.2f}" for stage in counted
    )


def _unmeasured_lines(missing: list[tuple[str, str | None]]) -> list[str]:
    """What the readout says about the weighted stages this run left out."""
    weight = sum(WEIGHTS[stage] for stage, _ in missing)
    lines = ["", f"НЕ ИЗМЕРЯЛОСЬ ({len(missing)}, вес {weight:.2f} — в итог не входит):"]
    for stage, reason in missing:
        tail = reason or f"в этот прогон не входил, добавь `--stage {stage}`"
        lines.append(f"  {stage} — {tail}")
    return lines


def _unmeasured(results: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
    """The weighted stages this run left unmeasured, with the reason where it gave one."""
    ran = {res["stage"]: res for res in results}
    out: list[tuple[str, str | None]] = []
    for stage in WEIGHTS:
        res = ran.get(stage)
        if res is None:
            out.append((stage, None))
        elif res.get("skipped"):
            out.append((stage, str(res["skipped"])))
    return out


def report(results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append("УЧАСТОК ФЛОУ            ЧЕКОВ   ПРОШЛО   ДОЛЯ")
    lines.append("─" * 52)
    weighted_sum = 0.0
    weight_total = 0.0
    for res in results:
        rows, counted = _stage_rows(res)
        lines.extend(rows)
        if res.get("skipped"):
            continue
        passed, total = counted
        share = passed / total if total else 0.0
        weight = WEIGHTS.get(res["stage"], 0.0)
        weighted_sum += share * weight
        weight_total += weight
    missing = _unmeasured(results)
    if weight_total:
        counted = [stage for stage in WEIGHTS if stage not in {s for s, _ in missing}]
        lines.append("─" * 52)
        lines.append(
            f"{'ИТОГО (по весам)':<22} {'':>6} {'':>8}   {weighted_sum / weight_total:>5.0%}"
        )
        lines.append(_counted_weights(counted))
    if missing:
        lines.extend(_unmeasured_lines(missing))

    gaps = [c for res in results for c in res["checks"] if c.get("known_gap")]
    if gaps:
        lines.append("")
        lines.append(f"ИЗВЕСТНЫЕ ПРОБЕЛЫ ({len(gaps)}) — видны, но не считаются провалом:")
        for c in gaps:
            lines.append(f"  [{c['case']}] {c['detail']}")
            lines.append(f"      {c['why']}")

    failures = [
        c for res in results for c in res["checks"] if not c["ok"] and not c.get("known_gap")
    ]
    lines.append("")
    if failures:
        lines.append(f"ТОЧКИ РОСТА ({len(failures)} проваленных проверок) — по убыванию частоты:")
        by_check: dict[str, int] = {}
        for c in failures:
            by_check[c["check"]] = by_check.get(c["check"], 0) + 1
        for check, count in sorted(by_check.items(), key=lambda kv: -kv[1]):
            lines.append(f"  • {check} — {count}")
        lines.append("")
        for c in failures:
            lines.append(f"  [{c['case']}] {c['check']}: {c['detail']}")
            lines.append(f"      зачем: {c['why']}")
    else:
        lines.append("Проваленных проверок нет.")
        lines.append("Это значит «поведение совпало с записанным контрактом» — не «продукт хорош»:")
        lines.append(
            "   набор кейсов узкий и написан нами, а качество маршрутов меряет benchmarks/."
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage evals for the Grodno guide")
    parser.add_argument("--stage", choices=sorted(STAGES), action="append")
    parser.add_argument(
        "--with-interpretation",
        action="store_true",
        help="include the live model stage (costs money, needs a key)",
    )
    parser.add_argument("--json", type=Path, default=None, help="write the raw report")
    args = parser.parse_args(argv)

    wanted = args.stage or ["verdicts", "services", "plan"]
    if args.with_interpretation and "interpretation" not in wanted:
        wanted.append("interpretation")

    results = [STAGES[name]() for name in wanted]
    print(report(results))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"отчёт записан: {args.json}")

    return (
        1
        if any(not c["ok"] and not c.get("known_gap") for res in results for c in res["checks"])
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())

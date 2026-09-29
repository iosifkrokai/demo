#!/usr/bin/env python
"""Stage evals for the Grodno guide: one runner, one honest report.

Why this exists next to `benchmarks/` instead of inside it. The benchmark there
grades *routes* (recall@K, order, detour, budget) against reference walks, and
`benchmarks/golden/` grades *request compliance* end-to-end. Both answer «как
получилось в целом». Neither can say **which part of the flow** produced a wrong
answer, and that is what tuning needs: a route can be mediocre while the reading
was perfect, and it can be perfect while the verifier lied.

So each stage gets its own cases and its own checks, and every check is a claim
that can be false:

  verdicts        does the deterministic verifier reach the right (status, reason)?
  services        does «что по пути» measure the truth? (cross-checked against an
                  independent implementation — PostGIS spheroid vs plain haversine)
  interpretation  did the request get read as it was meant? (live model path,
                  falls back to the deterministic parse — the report says which
                  answered, because a fallback run proves nothing about the model)

Run:
    ./.venv/bin/python evals/run.py                     # offline stages
    ./.venv/bin/python evals/run.py --stage services     # one stage
    ./.venv/bin/python evals/run.py --with-interpretation  # + the live model stage
    ./.venv/bin/python evals/run.py --json evals/last.json

Honesty rules this runner keeps, because the numbers are worthless without them:

* every case carries `why` — the defect it exists to catch; a case nobody can
  explain is deleted, not kept for the count;
* a stage that could not run (no key, no database) reports `skipped` and is
  excluded from the score — never counted as success;
* expectations that encode a judgement (which readings of «кофе по пути» are
  acceptable) list *all* acceptable answers, so the eval measures the contract
  instead of one preferred phrasing;
* the aggregate is a weighted check pass-rate, and the weights are a product
  judgement written down in WEIGHTS — they are meant to be argued with.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = Path(__file__).resolve().parent / "cases"

#: Weights over stages, as a product judgement: what a tourist feels.
#: Reading the request wrong poisons everything downstream, so it weighs most;
#: a verdict that claims «выполнено» without evidence and a fabricated service
#: are the two ways this guide could lie to someone standing in the street.
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
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Case(json.loads(line)))
        except (json.JSONDecodeError, KeyError) as exc:
            raise SystemExit(f"{path}:{lineno}: {exc}") from exc
    return out


# ── stage: verdicts ──────────────────────────────────────────────────────────

def run_verdicts() -> dict[str, Any]:
    """The deterministic verifier against the contract it must keep.

    Offline and total: no DB, no model, no network — a verdict that depends on
    the weather is not a contract.
    """
    from agent.planner.verify import ServiceAlongEvidence, verify
    from agent.requirements import Requirement, TripRequirements

    checks: list[dict[str, Any]] = []
    for case in _load("verdicts"):
        raw = case.raw
        reqs = TripRequirements(
            requirements=[Requirement(**dict(r)) for r in raw["requirements"]]
        )
        evidence = None
        if raw.get("evidence") is not None:
            evidence = ServiceAlongEvidence(
                measured=bool(raw["evidence"]["measured"]),
                by_code=raw["evidence"].get("by_code", {}),
            )
        try:
            result = verify(reqs, raw["plan"], raw.get("geometry"), evidence)
        except Exception as exc:  # a verifier that raises is a failed verdict
            checks.append(
                {"case": case.id, "check": "verdict", "ok": False,
                 "detail": f"{type(exc).__name__}: {exc}", "why": case.why}
            )
            continue

        got = [(r.status, r.reason) for r in result]
        want = [(e["status"], e["reason"]) for e in raw["expect"]]
        checks.append(
            {
                "case": case.id,
                "check": "verdict",
                "ok": got == want,
                # A documented gap stays visible but does not count as a failure:
                # hiding it would be dishonest, and scoring it as a defect would
                # make the number jump when nothing changed.
                "known_gap": bool(raw.get("known_gap")) and got != want,
                "detail": f"получено {got}, ожидалось {want}",
                "why": case.why,
            }
        )
    return {"stage": "verdicts", "checks": checks, "skipped": None}


# ── stage: services (cross-checked measurement) ───────────────────────────────

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8  # mean Earth radius, the sphere haversine assumes
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _off_line_and_along(
    lat: float, lon: float, line: list[tuple[float, float]]
) -> tuple[float, float]:
    """Independent reference: metres off the polyline and metres along it.

    Deliberately *not* PostGIS: the whole point is that a second implementation,
    written differently (equirectangular local projection, sphere distances),
    agrees with the database. Two independent methods agreeing is evidence; one
    method agreeing with itself is not.
    """
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
        t = 0.0 if seg_len == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len**2))
        cx, cy = ax + t * dx, ay + t * dy
        dist = math.hypot(px - cx, py - cy)
        along = run + t * seg_len
        if dist < best[0]:
            best = (dist, along, run + seg_len)
        run += seg_len
    total = run
    return best[0], (best[1] / total if total else 0.0)


def run_services() -> dict[str, Any]:
    """Does the measurement tell the truth about what lies beside the line?

    Two independent answers are compared: `agent.services` on PostGIS and a plain
    haversine/projection reference built here from the same rows. Points sitting
    within a few metres of the gate are reported separately rather than counted
    as disagreement — a spheroid and a sphere legitimately differ there, and
    pretending otherwise would turn a real tolerance into fake precision.
    """
    from psycopg.rows import dict_row

    from agent import services as services_mod, taxonomy
    from agent.clients_store import default_connect

    checks: list[dict[str, Any]] = []
    try:
        conn = default_connect()
    except Exception as exc:
        return {
            "stage": "services",
            "checks": checks,
            "skipped": f"нет доступа к базе: {type(exc).__name__}: {exc}",
        }

    near_gate = 0
    try:
        for case in _load("services_along"):
            raw = case.raw
            shape = raw["shape"]
            line = [(lat, lon) for lon, lat in shape["coordinates"]]
            codes = list(raw["categories"])
            gate = float(raw.get("gate_m") or services_mod.MAX_OFF_LINE_M[
                raw.get("profile", "pedestrian")
            ])

            answer = services_mod.services_along(
                conn, shape, categories=codes,
                profile=raw.get("profile", "pedestrian"),
                max_off_line_m=gate, limit=raw.get("limit", services_mod.MAX_SERVICES),
            )
            items = answer["items"]

            # Independent reference from the same rows, no PostGIS involved.
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT id, lat, lon, category FROM places WHERE category = ANY(%s) AND lat IS NOT NULL",
                    (codes,),
                )
                rows = cur.fetchall()
            ref: dict[int, tuple[float, float]] = {}
            for row in rows:
                off, along = _off_line_and_along(row["lat"], row["lon"], line)
                if off <= gate:
                    ref[row["id"]] = (off, along)

            # ── checks ──────────────────────────────────────────────────────
            # 1. Only services may come back, whatever was asked for.
            bad_role = [
                i["category"] for i in items
                if _role_of(taxonomy, i["category"]) != "service"
            ]
            checks.append({
                "case": case.id, "check": "role_only", "ok": not bad_role,
                "detail": f"не-услуги в ответе: {bad_role}", "why": case.why,
            })

            # 2. The answer must be ordered along the route.
            alongs = [i["along_m"] for i in items]
            checks.append({
                "case": case.id, "check": "ordered_along", "ok": alongs == sorted(alongs),
                "detail": f"along_m = {alongs}", "why": case.why,
            })

            # 3. Nothing may sit farther off the line than the gate.
            over = [i["off_line_m"] for i in items if i["off_line_m"] > gate + 1]
            checks.append({
                "case": case.id, "check": "within_gate", "ok": not over,
                "detail": f"за порогом {gate} м: {over}", "why": case.why,
            })

            # 4. Membership agrees with the independent reference (gate ties set aside).
            # The answer is capped by `limit` and ordered along the route, so the
            # reference must be capped the same way — otherwise a correct capped
            # answer looks like a pile of missing points.
            cap = int(raw.get("limit", services_mod.MAX_SERVICES))
            ref_capped = dict(
                sorted(ref.items(), key=lambda kv: kv[1][1])[:cap]
            )
            safe_ref = {
                pid for pid, (off, _) in ref_capped.items()
                if abs(off - gate) > 5 and gate - off > 5
            }
            safe_got = {
                i["id"] for i in items
                if abs(i["off_line_m"] - gate) > 5 and gate - i["off_line_m"] > 5
            }
            ties = len(ref_capped) - len(safe_ref)
            checks.append({
                "case": case.id, "check": "cap_matches_limit",
                "ok": len(items) <= cap,
                "detail": f"в ответе {len(items)} точек при пределе {cap}",
                "why": case.why,
            })
            near_gate += ties
            missing = safe_ref - safe_got
            extra = safe_got - safe_ref
            checks.append({
                "case": case.id, "check": "membership_agrees",
                "ok": not missing and not extra,
                "detail": f"нет в ответе {sorted(missing)}, лишние {sorted(extra)} (у порога не считаем: {ties})",
                "why": case.why,
            })

            # 5. For shared points the two distances must agree within tolerance.
            ref_off = {pid: off for pid, (off, _) in ref.items()}
            worst = 0.0
            allowance = 2.0
            for item in items:
                if item["id"] in ref_off:
                    diff = abs(item["off_line_m"] - ref_off[item["id"]])
                    # Both methods are approximations, and their difference grows
                    # with the distance: PostGIS measures on the spheroid, the
                    # reference on a sphere with a local projection. 0.5% of the
                    # distance plus 2 m is the honest envelope — a fixed 2 m
                    # would turn real geometry into a fake disagreement at 400 m.
                    allowance = max(allowance, 0.005 * ref_off[item["id"]] + 2.0)
                    worst = max(worst, diff)
            checks.append({
                "case": case.id, "check": "distance_agrees", "ok": worst <= allowance,
                "detail": f"максимальное расхождение {worst:.2f} м (допуск {allowance:.2f} м)",
                "why": case.why,
            })

            # 6. The answer must name what it measured and what it refused to.
            flags_ok = (
                answer.get("detour_confirmed") is False
                and answer.get("measured") == "distance_to_line"
                and answer.get("not_measured") == "detour_walking_time"
            )
            checks.append({
                "case": case.id, "check": "honest_flags", "ok": flags_ok,
                "detail": f"detour_confirmed={answer.get('detour_confirmed')}, "
                          f"measured={answer.get('measured')}, not_measured={answer.get('not_measured')}",
                "why": case.why,
            })

            # 7. A full answer must say that it was capped.
            if len(items) >= int(raw.get("limit", services_mod.MAX_SERVICES)):
                checks.append({
                    "case": case.id, "check": "cap_is_declared",
                    "ok": answer.get("capped") is True,
                    "detail": f"capped={answer.get('capped')} при {len(items)} точках",
                    "why": case.why,
                })

            # 8. The gate must widen with the profile, not shrink.
            if raw.get("monotonic_with_gate"):
                # The same cap on both calls: a wider gate must find *more*, and
                # comparing 36 against 53 would only measure the cap.
                cap_here = int(raw.get("limit", services_mod.MAX_SERVICES))
                wider = services_mod.services_along(
                    conn, shape, categories=codes, profile=raw["profile"],
                    max_off_line_m=gate * 3, limit=max(cap_here, cap_here * 3),
                )
                ok = len(wider["items"]) >= len(items)
                checks.append({
                    "case": case.id, "check": "wider_gate_finds_more", "ok": ok,
                    "detail": f"{len(items)} при {gate:.0f} м → {len(wider['items'])} при {gate * 3:.0f} м",
                    "why": case.why,
                })
    finally:
        conn.close()

    return {"stage": "services", "checks": checks, "skipped": None, "near_gate_points": near_gate}


def _role_of(taxonomy: Any, code: str) -> str:
    try:
        return taxonomy.role(code)
    except Exception:
        return "unknown"


# ── stage: plan ─────────────────────────────────────────────────────────────

#: Where the served app answers. The plan stage asks the app itself.
BASE_URL = os.environ.get("EVALS_BASE_URL", "http://localhost:8080")


def run_plan() -> dict[str, Any]:
    """What the walk is actually made of, over HTTP.

    This stage exists because the two defects it pins were invisible one level
    down: a stop pool that keeps a service, and a prohibition the plan violates,
    are properties of the *composed* route — the optimizer, the negative filter
    and the must-visit bypass agreeing with each other. It asks the served app,
    so a stale server answering with old code shows up as a failure of the
    request rather than a green run (that happened: an orphaned process held the
    port and the numbers looked fine).
    """
    from agent import taxonomy

    cases = _load("plan")
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append(
            {"case": case.id, "check": name, "ok": ok, "detail": detail,
             "why": case.why, "known_gap": False}
        )

    import urllib.error
    import urllib.request

    for case in cases:
        raw = case.raw
        body = json.dumps(
            {
                "query": raw["query"],
                "time_budget_minutes": raw.get("budget_minutes", 120),
                "profile": raw.get("profile", "pedestrian"),
                # A tourist standing somewhere: without a position the pipeline
                # has nothing to anchor the walk to and refuses the request, and
                # the case would test the refusal instead of the plan.
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
                if _role_of(taxonomy, s.get("category") or "") == "service"
            ]
            check(
                "stops_are_not_services", not offenders,
                f"услуги среди остановок: {offenders}" if offenders
                else f"{len(stops)} остановок, услуги среди них нет",
            )

        if raw.get("expect", {}).get("avoid_is_honoured"):
            forbidden = {
                r.get("code") for r in requirements
                if r.get("kind") == "avoid" and r.get("code")
            }
            verdicts = [
                (r.get("code"), r.get("status"), r.get("reason"))
                for r in requirements if r.get("kind") == "avoid"
            ]
            in_plan = [
                f"{s.get('name')} [{s.get('category')}]"
                for s in stops if (s.get("category") or "") in forbidden
            ]
            violated = [
                code for code, status, _reason in verdicts if status != "satisfied"
            ]
            ok = not in_plan and not violated
            detail = (
                f"запрет в плане: {in_plan}, вердикты: {verdicts}" if not ok
                else f"вердикты: {verdicts}, запрещённого в плане нет"
            )
            check("avoid_is_honoured", ok, detail)

        check(
            "request_is_answered", True,
            f"статус {payload.get('status')}, остановок {len(stops)}",
        )

    return {"stage": "plan", "checks": checks, "base_url": BASE_URL}


# ── stage: interpretation ────────────────────────────────────────────────────

def run_interpretation() -> dict[str, Any]:
    """Did the request get read as it was meant?

    Live: this stage asks the same entry point the product uses
    (`build_requirements`), which prefers the model and falls back to the
    deterministic parse. The report records which one answered — a fallback run
    says nothing about the model, and counting it as a pass would hide exactly
    the thing we want to tune.
    """
    from agent import taxonomy
    from agent.models import GenerateReq
    from agent.planner.intent import build_requirements

    checks: list[dict[str, Any]] = []
    sources: dict[str, int] = {}
    for case in _load("interpretation"):
        raw = case.raw
        req = GenerateReq(query=raw["query"], **(raw.get("filters") or {}))
        try:
            got = build_requirements(raw["query"], req)
        except Exception as exc:
            checks.append({
                "case": case.id, "check": "reads_request", "ok": False,
                "detail": f"{type(exc).__name__}: {exc}", "why": case.why,
            })
            continue

        source = str(getattr(got, "source", "?"))
        sources[source] = sources.get(source, 0) + 1
        reading = sorted(
            (r.kind, r.code or r.name or "-", r.strength) for r in got.requirements
        )

        # Acceptable readings are listed in the case: the eval measures the
        # contract, not one preferred phrasing.
        ok = False
        for acceptable in raw["acceptable"]:
            if all(
                # `code: null` in a case means «любой код» — for must_visit, which
                # is a place rather than a category.
                any(k == a["kind"] and (a.get("code") is None or c == a["code"])
                    and s == a["strength"]
                    for k, c, s in reading)
                for a in acceptable
            ):
                ok = True
                break

        checks.append({
            "case": case.id, "check": "reads_request", "ok": ok,
            "detail": f"источник={source}, прочитано {reading}",
            "why": case.why,
        })

        # A code nobody can look up must never appear as a requirement.
        invented = [
            r.code for r in got.requirements
            if r.code and not _code_exists(taxonomy, r.code)
        ]
        checks.append({
            "case": case.id, "check": "no_invented_codes", "ok": not invented,
            "detail": f"выдуманные коды: {invented}", "why": case.why,
        })

        # A mandatory place that resolved to nothing is a mandatory requirement
        # the guide can only report as unmet — the tourist asked for something
        # that does not exist in the data. Live examples: «Гродно за два часа»
        # made the *city* a must-visit, and «Старый и Новый замки» produced the
        # fragments «Старый»/«Новый». Whether it arrived with a name or without
        # one, what matters is that nothing is attached to it.
        unplaceable = [
            (r.name or r.code or "-") for r in got.requirements
            if r.kind == "must_visit" and r.place_id is None
        ]
        checks.append({
            "case": case.id, "check": "must_visit_is_placeable", "ok": not unplaceable,
            "detail": f"обязательные места, не привязанные к месту: {unplaceable}",
            "why": case.why,
        })

    # A case that documents a known defect reports it as a gap, not as a
    # failure — visible every run, without training everyone to ignore red.
    for check in checks:
        if not check["ok"] and any(
            c.raw.get("known_gap") and c.id == check["case"]
            for c in _load("interpretation")
        ):
            check["known_gap"] = True

    return {
        "stage": "interpretation", "checks": checks, "skipped": None,
        "sources": sources,
    }


def _code_exists(taxonomy: Any, code: str) -> bool:
    try:
        taxonomy.role(code)
        return True
    except Exception:
        return False


STAGES: dict[str, Callable[[], dict[str, Any]]] = {
    "verdicts": run_verdicts,
    "services": run_services,
    "interpretation": run_interpretation,
    "plan": run_plan,
}


# ── report ───────────────────────────────────────────────────────────────────

def _rate(checks: list[dict[str, Any]]) -> tuple[int, int]:
    """Pass rate over the checks that count; documented gaps are excluded."""
    counted = [c for c in checks if not c.get("known_gap")]
    return sum(1 for c in counted if c["ok"]), len(counted)


def report(results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append("УЧАСТОК ФЛОУ            ЧЕКОВ   ПРОШЛО   ДОЛЯ")
    lines.append("─" * 52)
    weighted_sum = 0.0
    weight_total = 0.0
    for res in results:
        stage = res["stage"]
        passed, total = _rate(res["checks"])
        if res.get("skipped"):
            lines.append(f"{stage:<22} {'—':>6} {'—':>8}   пропущен: {res['skipped']}")
            continue
        share = passed / total if total else 0.0
        weight = WEIGHTS.get(stage, 0.0)
        weighted_sum += share * weight
        weight_total += weight
        lines.append(f"{stage:<22} {total:>6} {passed:>8}   {share:>5.0%}")
        if res.get("sources"):
            lines.append(f"{'':<22} источник разбора: {res['sources']}")
        if res.get("near_gate_points"):
            lines.append(
                f"{'':<22} точек у самого порога (не считаем расхождением): "
                f"{res['near_gate_points']}"
            )
    if weight_total:
        lines.append("─" * 52)
        lines.append(
            f"{'ИТОГО (по весам)':<22} {'':>6} {'':>8}   {weighted_sum / weight_total:>5.0%}"
        )
        lines.append(
            "   веса: " + ", ".join(f"{k} {v:.2f}" for k, v in WEIGHTS.items())
        )

    gaps = [c for res in results for c in res["checks"] if c.get("known_gap")]
    if gaps:
        lines.append("")
        lines.append(f"ИЗВЕСТНЫЕ ПРОБЕЛЫ ({len(gaps)}) — видны, но не считаются провалом:")
        for c in gaps:
            lines.append(f"  [{c['case']}] {c['detail']}")
            lines.append(f"      {c['why']}")

    failures = [
        c for res in results for c in res["checks"]
        if not c["ok"] and not c.get("known_gap")
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
        lines.append(
            "Это значит «поведение совпало с записанным контрактом» — не «продукт хорош»:"
        )
        lines.append(
            "   набор кейсов узкий и написан нами, а качество маршрутов меряет benchmarks/."
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage evals for the Grodno guide")
    parser.add_argument("--stage", choices=sorted(STAGES), action="append")
    parser.add_argument(
        "--with-interpretation", action="store_true",
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
        args.json.write_text(
            json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"отчёт записан: {args.json}")

    # A documented gap is not a failure: the gate must agree with the rate it
    # prints, otherwise the number and the exit code tell different stories.
    return (
        1
        if any(
            not c["ok"] and not c.get("known_gap")
            for res in results
            for c in res["checks"]
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())

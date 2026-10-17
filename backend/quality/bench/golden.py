"""Golden runs, their snapshots and the compliance reports."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quality.bench.analysis import (
    _git_sha,
    _http_error_detail,
    _run_id,
    default_snapshot_dir,
    post_generate,
)
from quality.bench.cases import golden_by_id, load_golden_cases, parity_groups
from quality.bench.compliance import (
    build_golden_request,
    compliance_summary,
    evaluate_compliance,
    parity_verdict,
    response_total_minutes,
)
from quality.bench.config import (
    BENCH_GOLDEN,
    BENCH_OUT,
    CHECK_API_ERROR,
    GOLDEN_ROW_SCHEMA,
    GOLDEN_SNAPSHOT_FILE,
    GOLDEN_SNAPSHOT_SCHEMA,
    HEALTH_TIMEOUT_S,
    REPORT_SCHEMA,
    _r,
)
from quality.bench.models import ComplianceVerdict, GoldenCase, ReportInput


@dataclass
class GoldenRun:
    """One (case, repeat) of a golden run — what the report and the snapshot store."""

    case: GoldenCase
    repeat: int
    request: dict[str, Any]
    http_status: int | None = None
    api_error: str | None = None
    response: dict[str, Any] | None = None
    latency_s: float = 0.0
    verdict: ComplianceVerdict | None = None
    ts: str | None = None
    git_sha: str | None = None


def summarise_repeats(case: GoldenCase, runs: list[GoldenRun]) -> ComplianceVerdict:
    """One verdict per case over its repeats: PASS only when every repeat passed."""
    verdicts = [r.verdict for r in runs if r.verdict is not None]
    if not verdicts:
        return ComplianceVerdict(
            case_id=case.id,
            passed=False,
            reason=CHECK_API_ERROR,
            detail="no run produced a verdict",
        )
    failing = [v for v in verdicts if not v.passed]
    merged = dict(verdicts[0].checks)
    for verdict in verdicts[1:]:
        for code, check in verdict.checks.items():
            if check.get("ok") is False and merged.get(code, {}).get("ok") is not False:
                merged[code] = check
    if not failing:
        return ComplianceVerdict(
            case_id=case.id,
            passed=True,
            reason="ok",
            detail=f"{len(verdicts)}/{len(verdicts)} repeat(s) passed",
            status=verdicts[0].status,
            status_source=verdicts[0].status_source,
            checks=merged,
        )
    first = failing[0]
    return ComplianceVerdict(
        case_id=case.id,
        passed=False,
        reason=first.reason,
        detail=(
            f"{len(verdicts) - len(failing)}/{len(verdicts)} repeat(s) passed; "
            f"repeat failed with {first.reason}: {first.detail}"
        ),
        status=first.status,
        status_source=first.status_source,
        checks=merged,
    )


def run_golden(cases: list[GoldenCase], base_url: str, repeat: int) -> list[GoldenRun]:
    """Call the agent once per (case, repeat) and score each response."""
    git_sha = _git_sha()
    runs: list[GoldenRun] = []
    for case in cases:
        print(f"\n▶ {case.id}  [{case.locale}]")
        print(f"  query  : {case.query}")
        print(f"  filters: {json.dumps(case.filters, ensure_ascii=False)}")
        for i in range(repeat):
            request = build_golden_request(case)
            t0 = time.monotonic()
            status: int | None = None
            raw: dict[str, Any] | None = None
            err: str | None = None
            try:
                status, raw = post_generate(base_url, request)
            except urllib.error.HTTPError as exc:
                status = exc.code
                err = _http_error_detail(exc)
            except urllib.error.URLError as exc:
                err = str(exc)
            latency = time.monotonic() - t0
            verdict = evaluate_compliance(case, raw, http_status=status, api_error=err)
            run = GoldenRun(
                case=case,
                repeat=i + 1,
                request=request,
                http_status=status,
                api_error=err,
                response=raw,
                latency_s=latency,
                verdict=verdict,
                ts=datetime.now(UTC).isoformat(),
                git_sha=git_sha,
            )
            runs.append(run)
            print(
                f"  run {i + 1}/{repeat}: {'PASS' if verdict.passed else 'FAIL'} "
                f"[{verdict.reason}] status={verdict.status} ({verdict.status_source}) "
                f"stops={len((raw or {}).get('points') or [])} lat={latency:.2f}s"
            )
            if not verdict.passed:
                print(f"      ✗ {verdict.detail}")
            for code in verdict.unverified_checks:
                print(f"      ? {code}: {verdict.checks[code]['detail']}")
    return runs


def make_golden_row(run: GoldenRun, base_url: str) -> dict[str, Any]:
    return {
        "record": "golden_row",
        "schema": GOLDEN_ROW_SCHEMA,
        "case": run.case.id,
        "locale": run.case.locale,
        "repeat": run.repeat,
        "ts": run.ts,
        "git_sha": run.git_sha,
        "base_url": base_url,
        "request": run.request,
        "http_status": run.http_status,
        "api_error": run.api_error,
        "response": run.response,
        "latency_s": _r(run.latency_s, 3),
    }


def golden_snapshot_meta(
    cases: list[GoldenCase], base_url: str, repeat: int, *, git_sha: str | None = None
) -> dict[str, Any]:
    """The provenance header of a golden snapshot, written by both entry points."""
    return {
        "record": "meta",
        "schema": GOLDEN_SNAPSHOT_SCHEMA,
        "run_id": _run_id(),
        "started_at": datetime.now(UTC).isoformat(),
        "git_sha": git_sha if git_sha is not None else _git_sha(),
        "base_url": base_url,
        "repeat": repeat,
        "cases": [c.id for c in cases],
        "bench_version": REPORT_SCHEMA,
    }


def write_golden_snapshot(
    runs: list[GoldenRun],
    snapshot_dir: Path,
    base_url: str,
    *,
    append: bool = True,
    meta: dict[str, Any] | None = None,
) -> Path:
    """Write the golden snapshot (one meta record + one row per run)."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / GOLDEN_SNAPSHOT_FILE
    cases = [run.case for run in runs]
    header = meta or golden_snapshot_meta(cases, base_url, max((r.repeat for r in runs), default=1))
    with path.open("a" if append else "w", encoding="utf-8") as fh:
        fh.write(json.dumps(header, ensure_ascii=False) + "\n")
        for run in runs:
            fh.write(json.dumps(make_golden_row(run, base_url), ensure_ascii=False) + "\n")
    return path


def load_golden_snapshot(snapshot_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read golden_rows.jsonl → (meta, rows). Never touches the network."""
    path = Path(snapshot_dir) / GOLDEN_SNAPSHOT_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"no {GOLDEN_SNAPSHOT_FILE} in {snapshot_dir} "
            f"(a golden snapshot holds that file plus the reports)"
        )
    meta: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("record") == "meta":
                meta = meta or rec
            elif rec.get("record") == "golden_row":
                rows.append(rec)
    if not rows:
        raise ValueError(f"{snapshot_dir} holds no golden rows")
    rows.sort(key=lambda r: (str(r.get("case")), int(r.get("repeat", 0))))
    return meta, rows


def rescore_golden_rows(rows: list[dict[str, Any]], cases: list[GoldenCase]) -> list[GoldenRun]:
    """Recompute every verdict offline: no agent, no Valhalla, no clock."""
    by_id = golden_by_id(cases)
    out: list[GoldenRun] = []
    for row in rows:
        case = by_id.get(str(row.get("case")))
        if case is None:
            raise SystemExit(
                f"golden snapshot references case {row.get('case')!r} but "
                f"{BENCH_GOLDEN}/{(row.get('case') or '')}.json does not exist — "
                "cannot rescore"
            )
        verdict = evaluate_compliance(
            case,
            row.get("response"),
            http_status=row.get("http_status"),
            api_error=row.get("api_error"),
        )
        out.append(
            GoldenRun(
                case=case,
                repeat=int(row.get("repeat", 1)),
                request=row.get("request") or {},
                http_status=row.get("http_status"),
                api_error=row.get("api_error"),
                response=row.get("response"),
                latency_s=float(row.get("latency_s") or 0.0),
                verdict=verdict,
                ts=row.get("ts"),
                git_sha=row.get("git_sha"),
            )
        )
    return out


def golden_verdicts(
    runs: list[GoldenRun], cases: list[GoldenCase]
) -> tuple[dict[str, ComplianceVerdict], list[ComplianceVerdict]]:
    """Per-case verdicts (merged over repeats) + one verdict per parity group."""
    by_case: dict[str, list[GoldenRun]] = {}
    for run in runs:
        by_case.setdefault(run.case.id, []).append(run)
    verdicts = {
        case.id: summarise_repeats(case, sorted(by_case.get(case.id, []), key=lambda r: r.repeat))
        for case in cases
        if by_case.get(case.id)
    }
    parity = [
        parity_verdict(name, members, verdicts)
        for name, members in sorted(parity_groups(cases).items())
    ]
    return verdicts, parity


def print_compliance_table(
    cases: list[GoldenCase],
    verdicts: dict[str, ComplianceVerdict],
    parity: list[ComplianceVerdict],
    summary: dict[str, Any],
) -> None:
    width = 78
    print()
    print("═" * width)
    print("GOLDEN SET — requirement compliance (per case, machine-readable reason)")
    print("═" * width)
    print(f"{'case':<32} {'loc':<4} {'verdict':<7} {'status':<18} reason")
    print("─" * width)
    for case in cases:
        verdict = verdicts.get(case.id)
        if verdict is None:
            print(f"{case.id:<32} {case.locale:<4} {'NOT RUN':<7} {'—':<18} —")
            continue
        mark = "PASS" if verdict.passed else "FAIL"
        print(
            f"{case.id:<32} {case.locale:<4} {mark:<7} "
            f"{(verdict.status or '—'):<18} {verdict.reason}"
        )
        if not verdict.passed:
            print(f"{'':<44}└ {verdict.detail}")
        for code in verdict.unverified_checks:
            print(f"{'':<44}? {code}: {verdict.checks[code]['detail']}")
    for verdict in parity:
        mark = "PASS" if verdict.passed else "FAIL"
        print(f"{verdict.case_id:<32} {'ru/en':<4} {mark:<7} {'—':<18} {verdict.reason}")
        print(f"{'':<44}└ {verdict.detail}")
    print("─" * width)
    rate = summary["compliance_rate"]
    print(
        f"cases: {summary['n_cases_passed']}/{summary['n_cases']} passed · "
        f"parity groups: {summary['n_parity_passed']}/{summary['n_parity_groups']} passed"
    )
    print(
        f"COMPLIANCE RATE: {'n/a' if rate is None else f'{rate:.3f}'} "
        f"({summary['n_units_passed']}/{summary['n_units']} units; a unit is one "
        "case or one parity group)"
    )
    if summary["failures_by_reason"]:
        print(
            "failures by reason: "
            + ", ".join(f"{k}={v}" for k, v in summary["failures_by_reason"].items())
        )
    if summary["unverified_checks"]:
        print(
            f"unverifiable checks (the current API cannot answer them): "
            f"{', '.join(summary['unverified_checks'])}"
        )
    print("═" * width)
    print()


def write_compliance_reports(
    out_dir: Path,
    report: ReportInput,
    *,
    cases: list[GoldenCase],
    runs: list[GoldenRun],
    summary: dict[str, Any],
) -> None:
    """compliance.json + compliance.md + compliance.metrics.jsonl (one line/case)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    per_case: dict[str, list[GoldenRun]] = {}
    for run in runs:
        per_case.setdefault(run.case.id, []).append(run)

    payload = {
        "schema": REPORT_SCHEMA,
        "mode": report.mode,
        "generated_at": report.generated_at,
        "base_url": report.base_url,
        "snapshot": report.snapshot,
        "summary": summary,
        "cases": [
            {
                "id": case.id,
                "locale": case.locale,
                "query": case.query,
                "parity_group": case.parity_group,
                "filters": case.filters,
                "expectations": case.expectations,
                "runs": [
                    {
                        "repeat": run.repeat,
                        "http_status": run.http_status,
                        "api_error": run.api_error,
                        "latency_s": _r(run.latency_s, 3),
                        "total_minutes": _r(
                            response_total_minutes(
                                run.response,
                                [
                                    p
                                    for p in ((run.response or {}).get("points") or [])
                                    if isinstance(p, dict)
                                ],
                            ),
                            2,
                        ),
                        "n_stops": len((run.response or {}).get("points") or []),
                        "verdict": run.verdict.as_dict() if run.verdict else None,
                    }
                    for run in sorted(per_case.get(case.id, []), key=lambda r: r.repeat)
                ],
            }
            for case in cases
        ],
    }
    (out_dir / "compliance.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        json.dumps(
            {
                "case": v["case_id"],
                "passed": v["passed"],
                "reason": v["reason"],
                "status": v["status"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for v in summary["cases"]
    ]
    lines.append(
        json.dumps(
            {
                "case": "__overall__",
                "compliance_rate": _r(summary["compliance_rate"]),
                "n_units": summary["n_units"],
                "n_units_passed": summary["n_units_passed"],
                "failures_by_reason": summary["failures_by_reason"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    (out_dir / "compliance.metrics.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    rate = summary["compliance_rate"]
    compliance_text = "n/a" if rate is None else format(rate, ".3f")

    md = [
        "# Golden-set compliance report",
        "",
        (
            f"**Mode:** `{report.mode}` · **Generated:** {report.generated_at} · "
            f"**API:** `{report.base_url}`"
        ),
        "",
        (
            "This measures whether the CONDITIONS of a request survived, not how close "
            "the route came to a reference walk. A case passes only if every "
            "machine-checkable expectation of its file holds; a parity group passes "
            "only if RU and EN got the same kind of answer."
        ),
        "",
        (
            f"**Compliance rate: "
            f"{compliance_text}** "
            f"({summary['n_units_passed']}/{summary['n_units']} units)"
        ),
        "",
        "| case | locale | verdict | status | reason | detail |",
        "|---|---|---|---|---|---|",
    ]
    for entry in summary["cases"]:
        md.append(
            f"| {entry['case_id']} | — | {'PASS' if entry['passed'] else 'FAIL'} | "
            f"{entry['status']} | {entry['reason']} | {entry['detail']} |"
        )
    for entry in summary["parity"]:
        md.append(
            f"| {entry['case_id']} | ru/en | {'PASS' if entry['passed'] else 'FAIL'} | "
            f"{entry['status']} | {entry['reason']} | {entry['detail']} |"
        )
    if summary["failures_by_reason"]:
        md += ["", "## Failures by reason", ""]
        md += [f"- `{k}`: {v}" for k, v in summary["failures_by_reason"].items()]
    if summary["unverified_checks"]:
        md += [
            "",
            "## Checks the current API cannot answer",
            "",
            (
                "These are recorded as unverified rather than passed or failed, "
                "because the response carries no field to decide them:"
            ),
            "",
        ]
        md += [f"- `{c}`" for c in summary["unverified_checks"]]
    (out_dir / "compliance.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def fetch_health(base_url: str, timeout: float = HEALTH_TIMEOUT_S) -> dict[str, Any]:
    """GET /health → dict. Raises on transport failure (caller decides)."""
    req = urllib.request.Request(
        f"{base_url}/health",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def preflight_or_exit(base_url: str) -> dict[str, Any]:
    """Refuse to benchmark a backend that is not there."""
    try:
        health = fetch_health(base_url)
    except urllib.error.HTTPError as exc:
        _no_report(f"backend answered /health with HTTP {exc.code} at {base_url}")
    except urllib.error.URLError as exc:
        _no_report(f"backend not reachable at {base_url} ({exc.reason})")
    except (TimeoutError, OSError, ValueError) as exc:
        _no_report(f"backend /health at {base_url} failed ({type(exc).__name__}: {exc})")
    if not health.get("db"):
        _no_report(
            f"backend is up at {base_url} but its database is not reachable "
            "(/health db=false) — every route would come back empty"
        )
    if not health.get("valhalla"):
        _no_report(
            f"Valhalla is not reachable through the backend at {base_url} "
            "(/health valhalla=false) — route geometry and leg times would be fake"
        )
    if not health.get("llm") and not health.get("embedder"):
        print(
            "warning: no LLM/embedder key configured (/health llm=false): the "
            "pipeline will run its deterministic fallback, so this run does not "
            "measure the intent layer.",
            file=sys.stderr,
        )
    return health


def _no_report(message: str) -> None:
    print(f"bench_routes: {message}", file=sys.stderr)
    print(
        "bench_routes: refusing to run — a report written without a live backend "
        "would be a fabricated measurement, not a benchmark.",
        file=sys.stderr,
    )
    sys.exit(2)


def _golden_output(args) -> tuple[list[GoldenRun], list[GoldenCase], Path, dict[str, Any], str]:
    cases = load_golden_cases(Path(args.golden_dir) if args.golden_dir else BENCH_GOLDEN)
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c.id in wanted]
        missing = wanted - {c.id for c in cases}
        if missing:
            print(f"No golden case for: {sorted(missing)}", file=sys.stderr)
            sys.exit(1)
    if args.snapshot is None:
        snapshot_dir: Path | None = None
    elif args.snapshot:
        snapshot_dir = Path(args.snapshot)
    else:
        snapshot_dir = default_snapshot_dir()
    print("Golden compliance run — live")
    print(f"API base URL: {args.base_url}")
    print(f"Cases: {len(cases)} · runs per case: {args.repeat}")
    print(f"Snapshot: {snapshot_dir}" if snapshot_dir else "Snapshot: off")
    out_dir = Path(args.report_dir) if args.report_dir else BENCH_OUT
    print(f"Reports will be written to: {out_dir}")
    runs = run_golden(cases, args.base_url, args.repeat)
    if snapshot_dir is not None:
        write_golden_snapshot(runs, snapshot_dir, args.base_url, append=args.append)
        print(f"  golden snapshot → {snapshot_dir / GOLDEN_SNAPSHOT_FILE}")
    return runs, cases, out_dir, {"dir": str(snapshot_dir) if snapshot_dir else None}, "golden-live"


def _replay_golden_output(
    args: Any,
) -> tuple[list[GoldenRun], list[GoldenCase], Path, dict[str, Any], str]:
    snapshot_dir = Path(args.replay_golden)
    print(f"Golden replay — {snapshot_dir} (offline: no agent, no Valhalla, no clock)")
    cases = load_golden_cases(Path(args.golden_dir) if args.golden_dir else BENCH_GOLDEN)
    meta, rows = load_golden_snapshot(snapshot_dir)
    print(
        f"Snapshot: {len(rows)} row(s), cases {meta.get('cases')}, "
        f"git {meta.get('git_sha')}, taken {meta.get('started_at')}"
    )
    runs = rescore_golden_rows(rows, cases)
    return (
        runs,
        cases,
        Path(args.report_dir) if args.report_dir else snapshot_dir,
        {
            "dir": str(snapshot_dir),
            "n_rows": len(rows),
            "git_sha": meta.get("git_sha"),
            "generated_at": meta.get("started_at"),
        },
        "golden-replay",
    )


def emit_golden(args, out: tuple) -> None:
    runs, cases, out_dir, snapshot, mode = out
    verdicts, parity = golden_verdicts(runs, cases)
    summary = compliance_summary(cases, verdicts, parity)
    print_compliance_table(cases, verdicts, parity, summary)
    report = ReportInput(
        mode=mode,
        generated_at=(
            datetime.now(UTC).isoformat()
            if mode == "golden-live"
            else str(snapshot.get("generated_at") or "replay")
        ),
        base_url=args.base_url,
        snapshot=snapshot,
    )
    write_compliance_reports(out_dir, report, cases=cases, runs=runs, summary=summary)
    print(f"  compliance report → {out_dir / 'compliance.json'}")
    print(f"  compliance report → {out_dir / 'compliance.md'}")
    print(f"  compliance JSONL  → {out_dir / 'compliance.metrics.jsonl'}")

    rate = summary["compliance_rate"]
    if args.min_compliance is not None and (rate is None or rate + 1e-9 < args.min_compliance):
        print(
            f"--min-compliance {args.min_compliance}: measured "
            f"{'n/a' if rate is None else f'{rate:.3f}'}",
            file=sys.stderr,
        )
        sys.exit(1)
    parity_incomplete = summary["n_parity_passed"] != summary["n_parity_groups"]
    if args.strict and (summary["n_cases_failed"] or parity_incomplete):
        print(
            f"--strict: {summary['n_cases_failed']} golden case(s) failed, "
            f"{summary['n_parity_groups'] - summary['n_parity_passed']} parity group(s) failed",
            file=sys.stderr,
        )
        sys.exit(1)

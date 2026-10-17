"""The command line: parse, select the run, emit it."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from quality.bench.analysis import default_snapshot_dir, golden_sha256
from quality.bench.cases import load_curated_routes, load_golden_map, load_golden_routes
from quality.bench.config import (
    BACKEND,
    BENCH_CURATED,
    BENCH_CURATED_OUT,
    BENCH_OUT,
    BENCH_ROUTES,
    BOOTSTRAP_SAMPLES,
    BOOTSTRAP_SEED,
)
from quality.bench.golden import (
    _golden_output,
    _replay_golden_output,
    emit_golden,
    preflight_or_exit,
)
from quality.bench.models import GoldenRoute, ReportInput, RunRecord
from quality.bench.report import (
    failure_summary,
    noise_floor,
    overall_stats,
    per_case_scores,
    print_ci_table,
    print_failures,
    print_table,
    write_json_report,
    write_md_report,
    write_metrics_jsonl,
)
from quality.bench.run import (
    _snapshot_info,
    compare_snapshots,
    print_compare,
    run_live,
    run_replay,
    write_compare_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Golden-set route benchmark runner (snapshot / replay / compare).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8080",
        help="Agent API base URL (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--report-dir",
        default=None,
        help="Directory for report.json / report.md / report.metrics.jsonl "
        "(default: backend/quality/reports, or the snapshot dir under --replay)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help=(
            "Run each reference walk N times and report mean±spread per metric "
            "(default: 1). Each repeat is one POST /routes/generate, so the "
            "default keeps the previous single-pass cost."
        ),
    )
    parser.add_argument(
        "--case",
        action="append",
        metavar="ID",
        help="Only run/score these cases (the .json stem); repeatable",
    )
    parser.add_argument(
        "--snapshot",
        nargs="?",
        const="",
        metavar="DIR",
        help=(
            "Record one JSONL row per (case, repeat) so the run can be replayed. "
            "With no value: backend/quality/reports/snapshots/<date>."
        ),
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to an existing snapshot instead of starting a new file",
    )
    parser.add_argument(
        "--replay",
        metavar="DIR",
        help="Recompute every metric offline from a snapshot (no agent calls)",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("SNAPSHOT_A", "SNAPSHOT_B"),
        help="Paired bootstrap A − B over the cases the two snapshots share",
    )
    parser.add_argument(
        "--bootstrap",
        type=int,
        default=BOOTSTRAP_SAMPLES,
        metavar="B",
        help=f"Bootstrap resamples (default: {BOOTSTRAP_SAMPLES})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=BOOTSTRAP_SEED,
        help=f"Bootstrap RNG seed — fixed so replays are byte-identical "
        f"(default: {BOOTSTRAP_SEED})",
    )
    parser.add_argument(
        "--curated",
        action="store_true",
        help=(
            "Score the READY-MADE routes (quality/cases/curated/*.json) instead of the "
            "benchmark walks: each case asks the planner the same request the itinerary "
            "answers, and the itinerary's own stops are the reference. Needs a live stack."
        ),
    )
    parser.add_argument(
        "--curated-dir",
        default=None,
        metavar="DIR",
        help="Directory of curated cases (default: backend/quality/cases/curated)",
    )
    parser.add_argument(
        "--golden",
        action="store_true",
        help=(
            "Score the REQUIREMENT-COMPLIANCE set (quality/cases/compliance/*.json) "
            "instead of the reference walks: per-case PASS/FAIL with a "
            "machine-readable reason plus an aggregate compliance rate. Needs a "
            "live backend and Valhalla; refuses to write a report without them."
        ),
    )
    parser.add_argument(
        "--golden-dir",
        default=None,
        metavar="DIR",
        help="Directory of golden cases (default: backend/quality/cases/compliance)",
    )
    parser.add_argument(
        "--replay-golden",
        metavar="DIR",
        help=(
            "Rescore a recorded golden run (DIR/golden_rows.jsonl) offline — no "
            "agent, no Valhalla, no clock."
        ),
    )
    parser.add_argument(
        "--min-compliance",
        type=float,
        default=None,
        metavar="FLOAT",
        help=(
            "Golden mode: exit 1 when the compliance rate is below FLOAT "
            "(e.g. 0.9). Unset: the rate is reported, not gated."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 when any hard failure was recorded (route mode) or any "
            "golden case / parity group failed (golden mode)"
        ),
    )
    return parser


@dataclass
class RunOutput:
    """Everything one run of the harness produced, ready to be reported on."""

    mode: str
    groups: list[list[RunRecord]]
    meta: dict[str, Any]
    out_dir: Path
    snapshot: dict[str, Any]
    generated_at: str
    drift: list[dict[str, Any]] = field(default_factory=list)


def _select_routes(args) -> list[GoldenRoute]:
    if args.curated:
        source = Path(args.curated_dir) if args.curated_dir else BENCH_CURATED
        routes = load_curated_routes(source)
        if not routes:
            print(f"No curated cases found in {source}", file=sys.stderr)
            sys.exit(1)
    else:
        routes = load_golden_routes(BENCH_ROUTES)
        if not routes:
            print(f"No benchmark files found in {BENCH_ROUTES}", file=sys.stderr)
            sys.exit(1)
    if args.case:
        wanted = set(args.case)
        routes = [r for r in routes if r.case in wanted]
        missing = wanted - {r.case for r in routes}
        if missing:
            print(f"No case file for case(s): {sorted(missing)}", file=sys.stderr)
            sys.exit(1)
    return routes


def _cases_dir(cases: list[str]) -> Path | None:
    """The one directory a run's cases come from, when they all come from one."""
    goldens = load_golden_map()
    dirs = {goldens[c].path.parent for c in cases if c in goldens and goldens[c].path}
    dirs = {d for d in dirs if d is not None}
    return dirs.pop() if len(dirs) == 1 else None


def _relative_cases_dir(groups: list[list[RunRecord]]) -> str:
    """The set the run scored, as a path from backend/ — the report names its own source."""
    found = _cases_dir([g[0].case for g in groups if g])
    if found is None:
        return ""
    try:
        return str(found.relative_to(BACKEND))
    except ValueError:
        return str(found)


def _report_dir(args, fallback: Path, cases: list[str]) -> Path:
    """Where a run's reports land: an explicit dir, or the measured set's own.

    A replay cannot be told its set by a flag, so the cases decide: rows that name the
    ready-made walks are filed with them, and a report never overwrites the other
    layer's numbers under the other layer's name.
    """
    if args.report_dir:
        return Path(args.report_dir)
    return BENCH_CURATED_OUT if _cases_dir(cases) == BENCH_CURATED else fallback


def _live_output(args, routes: list[GoldenRoute]) -> RunOutput:
    snapshot_dir: Path | None
    if args.snapshot is None:
        snapshot_dir = None
    elif args.snapshot:
        snapshot_dir = Path(args.snapshot)
    else:
        snapshot_dir = default_snapshot_dir()
    out_dir = _report_dir(args, BENCH_OUT, [r.case for r in routes])
    print("Benchmark runner — live")
    print(f"API base URL: {args.base_url}")
    print(f"Runs per case: {args.repeat}")
    print(f"Snapshot: {snapshot_dir}" if snapshot_dir else "Snapshot: off")
    print(f"Reports will be written to: {out_dir}")
    preflight_or_exit(args.base_url)
    groups, meta = run_live(routes, args.base_url, args.repeat, snapshot_dir, args.append)
    n_rows = sum(len(g) for g in groups)
    return RunOutput(
        mode="live",
        groups=groups,
        meta=meta,
        out_dir=out_dir,
        snapshot=_snapshot_info(meta, snapshot_dir, n_rows) if snapshot_dir else {},
        generated_at=str(meta.get("started_at")),
    )


def _replay_output(args) -> RunOutput:
    snapshot_dir = Path(args.replay)
    print(f"Replay — {snapshot_dir} (no agent, no Valhalla, offline)")
    groups, meta, drift = run_replay(snapshot_dir)
    n_rows = sum(len(g) for g in groups)
    print(
        f"Snapshot: {n_rows} row(s), {len(groups)} case(s), "
        f"git {meta.get('git_sha')}, taken {meta.get('started_at')}"
    )
    recorded = meta.get("golden_sha256") or {}
    if recorded:
        goldens = load_golden_map()
        drifted = [
            c for c, sha in recorded.items() if c in goldens and golden_sha256(goldens[c]) != sha
        ]
        if drifted:
            print(f"  ⚠ reference .json changed since the snapshot for: {drifted}")
    return RunOutput(
        mode="replay",
        groups=groups,
        meta=meta,
        out_dir=_report_dir(args, snapshot_dir, [g[0].case for g in groups if g]),
        snapshot=_snapshot_info(meta, snapshot_dir, n_rows),
        generated_at=str(meta.get("started_at") or "unknown"),
        drift=drift,
    )


def _emit(args, out: RunOutput) -> None:
    per_case = per_case_scores(out.groups)
    overall = overall_stats(per_case, args.bootstrap, args.seed)
    noise = noise_floor(out.groups)
    failures = failure_summary(out.groups)

    print_table(out.groups)
    print_ci_table(overall, per_case, noise, args.bootstrap, args.seed)
    print_failures(failures, overall)

    report = ReportInput(
        mode=out.mode,
        generated_at=out.generated_at,
        snapshot=out.snapshot,
        cases_dir=_relative_cases_dir(out.groups),
        overall=overall,
        noise=noise,
        failures=failures,
        drift_rows=out.drift,
        samples=args.bootstrap,
        seed=args.seed,
    )
    write_json_report(out.groups, out.out_dir, report, per_case=per_case)
    write_md_report(out.groups, out.out_dir, report)
    write_metrics_jsonl(out.groups, out.out_dir, overall, failures)

    if args.strict and failures["n_failed_runs"]:
        print(f"--strict: {failures['n_failed_runs']} hard failure(s)", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.repeat < 1:
        parser.error("--repeat must be >= 1")
    if args.bootstrap < 1:
        parser.error("--bootstrap must be >= 1")
    if args.min_compliance is not None and not (0.0 <= args.min_compliance <= 1.0):
        parser.error("--min-compliance must be between 0.0 and 1.0")

    if args.compare:
        dir_a, dir_b = (Path(p) for p in args.compare)
        cmp = compare_snapshots(dir_a, dir_b, args.bootstrap, args.seed)
        print_compare(cmp)
        out_dir = Path(args.report_dir) if args.report_dir else BENCH_OUT
        write_compare_report(cmp, out_dir)
        return

    if args.replay_golden:
        emit_golden(args, _replay_golden_output(args))
        return
    if args.golden:
        preflight_or_exit(args.base_url)
        emit_golden(args, _golden_output(args))
        return

    out = _replay_output(args) if args.replay else _live_output(args, _select_routes(args))
    _emit(args, out)


if __name__ == "__main__":
    main()

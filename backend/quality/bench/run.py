"""Live and replay route runs, and snapshot comparison."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quality.bench.analysis import (
    MIN_TAU_STOPS,
    _git_sha,
    _http_error_detail,
    _mean,
    _run_id,
    _spread,
    append_row,
    build_request,
    golden_sha256,
    load_snapshot,
    make_meta,
    make_row,
    paired_bootstrap,
    post_generate,
    resample_indices,
    route_origin,
    score_response,
    write_meta,
)
from quality.bench.cases import load_golden_map
from quality.bench.config import (
    _PAIR,
    BENCH_ROUTES,
    BOOTSTRAP_ALPHA,
    REFERENCE_NOISE_FLOOR,
    SNAPSHOT_SCHEMA,
)
from quality.bench.models import GoldenRoute, RunRecord
from quality.bench.report import COMPARE_METRICS, _attr, _f, failure_summary, per_case_scores


def run_live(
    routes: list[GoldenRoute],
    base_url: str,
    repeat: int,
    snapshot_dir: Path | None,
    append: bool,
) -> tuple[list[list[RunRecord]], dict[str, Any]]:
    """Call the agent once per (case, repeat); optionally snapshot every run."""
    started_at = datetime.now(UTC).isoformat()
    run_id = _run_id()
    git_sha = _git_sha()
    meta = make_meta(
        cases=routes,
        base_url=base_url,
        repeat=repeat,
        started_at=started_at,
        run_id=run_id,
        git_sha=git_sha,
    )
    rows_path = write_meta(meta, snapshot_dir, append) if snapshot_dir else None

    groups: list[list[RunRecord]] = []
    for route in routes:
        print(f"\n▶ {route.name}  [{route.case}]")
        print(f"  query : {route.query_ru}")
        print(f"  source: {route.source}")
        runs: list[RunRecord] = []
        for i in range(repeat):
            rec = _run_once(route, base_url, i + 1, git_sha)
            runs.append(rec)
            if rows_path is not None:
                append_row(make_row(rec, base_url), rows_path)
            _print_run(rec, i + 1, repeat, route)
        groups.append(runs)
    return groups, meta


def _run_once(route: GoldenRoute, base_url: str, repeat: int, git_sha: str) -> RunRecord:
    lat, lon = route_origin(route)
    request = build_request(route.query_ru, route.budget_minutes, lat, lon, route.profile)
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
    result = score_response(route, raw, http_status=status, api_error=err, latency_s=latency)
    return RunRecord(
        case=route.case,
        case_name=route.name,
        repeat=repeat,
        result=result,
        request=request,
        response=raw,
        ts=datetime.now(UTC).isoformat(),
        git_sha=git_sha,
    )


def _print_run(rec: RunRecord, i: int, repeat: int, route: GoldenRoute) -> None:
    r = rec.result
    if r.api_error:
        print(f"  run {i}/{repeat} ✗ API error: {r.api_error}")
        return
    shared = f"{r.shared_stops}/{len(route.stops)}"
    print(
        f"  run {i}/{repeat}: stops={r.n_stops} shared={shared} "
        f"S1={_f(r.stage.recall, 3)} S2={_f(r.stage.route_recall_given_pool, 3)} "
        f"recall@K={r.recall_at_k:.3f} prec={r.precision:.3f} "
        f"τ={_f(r.kendall_tau, 3)} detour={_f(r.detour_km, 3)}km "
        f"Δwalk={_f(r.walk_diff_min, 1)}min fit={'✓' if r.budget_fit else '✗'} "
        f"maxleg={_f(r.leg.max_leg_km_straight, 2)}km dup={r.leg.n_duplicate_stops} "
        f"unr={r.leg.n_unreachable_legs} lat={r.latency_s:.2f}s"
    )
    for f in r.failures:
        print(f"      {'✗' if f.gated else '!'} {f.kind}: {f.detail}")


def run_replay(
    snapshot_dir: Path,
) -> tuple[list[list[RunRecord]], dict[str, Any], list[dict[str, Any]]]:
    """Recompute every metric offline from a snapshot. No clock, no RNG, no I/O."""
    meta, rows = load_snapshot(snapshot_dir)
    goldens = load_golden_map()
    drift: list[dict[str, Any]] = []
    groups: dict[str, list[RunRecord]] = {}
    for row in rows:
        case = str(row.get("case"))
        golden = goldens.get(case)
        if golden is None:
            raise SystemExit(
                f"snapshot references case '{case}' but "
                f"{BENCH_ROUTES}/{case}.json no longer exists — cannot rescore"
            )
        recorded_sha = row.get("golden_sha256")
        current_sha = golden_sha256(golden)
        if (
            recorded_sha
            and current_sha
            and recorded_sha != current_sha
            and not any(d.get("case") == case and "golden" in d.get("note", "") for d in drift)
        ):
            drift.append(
                {
                    "case": case,
                    "recorded": recorded_sha,
                    "current": current_sha,
                    "note": "golden reference .json changed after the snapshot was taken",
                }
            )
        recorded_metrics = row.get("metrics")
        result = score_response(
            golden,
            row.get("response"),
            http_status=row.get("http_status"),
            api_error=row.get("api_error"),
            latency_s=float((recorded_metrics or {}).get("latency_s") or 0.0),
        )
        if recorded_metrics and recorded_metrics != result.metrics_dict():
            drift.append(
                {
                    "case": case,
                    "repeat": row.get("repeat"),
                    "note": "recomputed metrics differ from the snapshot's metrics",
                }
            )
        rec = RunRecord(
            case=case,
            case_name=str(row.get("case_name") or golden.name),
            repeat=int(row.get("repeat", 1)),
            result=result,
            request=row.get("request"),
            response=row.get("response"),
            ts=row.get("ts"),
            git_sha=row.get("git_sha"),
            recorded_metrics=recorded_metrics,
        )
        groups.setdefault(case, []).append(rec)
    ordered = [groups[c] for c in sorted(groups)]
    for g in ordered:
        g.sort(key=lambda r: r.repeat)
    return ordered, meta, drift


def _snapshot_info(meta: dict[str, Any], snapshot_dir: Path, n_rows: int) -> dict[str, Any]:
    return {
        "dir": str(snapshot_dir),
        "schema": meta.get("schema", SNAPSHOT_SCHEMA),
        "run_id": meta.get("run_id"),
        "started_at": meta.get("started_at"),
        "git_sha": meta.get("git_sha"),
        "base_url": meta.get("base_url"),
        "n_rows": n_rows,
        "cases": meta.get("cases", []),
    }


def compare_snapshots(dir_a: Path, dir_b: Path, samples: int, seed: int) -> dict[str, Any]:
    """Paired bootstrap A − B over the cases the two snapshots share."""
    groups_a, meta_a, _ = run_replay(dir_a)
    groups_b, meta_b, _ = run_replay(dir_b)
    per_a = per_case_scores(groups_a)
    per_b = per_case_scores(groups_b)
    shared_cases = sorted(set(per_a) & set(per_b))
    only_a = sorted(set(per_a) - set(per_b))
    only_b = sorted(set(per_b) - set(per_a))
    fails_a = failure_summary(groups_a)
    fails_b = failure_summary(groups_b)

    rows = []
    for path, label in COMPARE_METRICS:
        a_vals, b_vals = [], []
        for case in shared_cases:
            va, vb = per_a[case].get(path), per_b[case].get(path)
            if va is None or vb is None:
                continue
            a_vals.append(va)
            b_vals.append(vb)
        stats = (
            paired_bootstrap(a_vals, b_vals, resample_indices(len(a_vals), samples, seed))
            if a_vals
            else {"diff": None, "lo": None, "hi": None, "p": None, "n": 0}
        )
        stats["label"] = label
        stats["mean_a"] = (sum(a_vals) / len(a_vals)) if a_vals else None
        stats["mean_b"] = (sum(b_vals) / len(b_vals)) if b_vals else None
        stats["n_paired"] = len(a_vals)
        stats["noise_floor_a"] = _noise_of(groups_a, path)
        stats["noise_floor_b"] = _noise_of(groups_b, path)
        floors = [f for f in (stats["noise_floor_a"], stats["noise_floor_b"]) if f is not None]
        stats["noise_floor"] = max(floors) if floors else None
        stats["exceeds_noise_floor"] = (
            None
            if stats["noise_floor"] is None or stats["diff"] is None
            else abs(stats["diff"]) > stats["noise_floor"]
        )
        rows.append(stats)

    return {
        "a": {
            **_snapshot_info(meta_a, dir_a, sum(len(g) for g in groups_a)),
            "n_runs": sum(len(g) for g in groups_a),
            "n_hard_failure_runs": fails_a["n_failed_runs"],
            "n_leg_sanity_defects": fails_a["leg_sanity_defects"],
        },
        "b": {
            **_snapshot_info(meta_b, dir_b, sum(len(g) for g in groups_b)),
            "n_runs": sum(len(g) for g in groups_b),
            "n_hard_failure_runs": fails_b["n_failed_runs"],
            "n_leg_sanity_defects": fails_b["leg_sanity_defects"],
        },
        "shared_cases": shared_cases,
        "only_in_a": only_a,
        "only_in_b": only_b,
        "bootstrap": {
            "samples": samples,
            "seed": seed,
            "alpha": BOOTSTRAP_ALPHA,
            "paired": True,
            "unit": "case",
        },
        "reference_noise_floor": REFERENCE_NOISE_FLOOR,
        "metrics": rows,
    }


def _noise_of(groups: list[list[RunRecord]], path: str) -> float | None:
    spreads = []
    for g in groups:
        vals = _attr([r.result for r in g], path)
        if len(vals) < _PAIR:
            continue
        spreads.append(_spread(vals) or 0.0)
    return _mean(spreads)


def print_compare(cmp: dict[str, Any]) -> None:
    a, b = cmp["a"], cmp["b"]
    boot = cmp["bootstrap"]
    floor_of_one = 1.0 / boot["samples"]
    print()
    print("═" * 104)
    print("PAIRED BOOTSTRAP — snapshot A vs snapshot B (resampled unit: case)")
    print(
        f"  A: {a['dir']}  ({a['n_runs']} runs, git {a.get('git_sha')}, "
        f"{a.get('started_at')}, hard failures {a['n_hard_failure_runs']})"
    )
    print(
        f"  B: {b['dir']}  ({b['n_runs']} runs, git {b.get('git_sha')}, "
        f"{b.get('started_at')}, hard failures {b['n_hard_failure_runs']})"
    )
    print(
        f"  paired over {len(cmp['shared_cases'])} shared case(s): "
        f"{', '.join(cmp['shared_cases']) or '—'}"
    )
    if cmp["only_in_a"] or cmp["only_in_b"]:
        print(f"  ⚠ only in A: {cmp['only_in_a'] or '—'} · only in B: {cmp['only_in_b'] or '—'}")
    print(f"  B={boot['samples']}, seed={boot['seed']}, alpha={boot['alpha']}")
    print("─" * 104)
    print(
        f"{'metric':<30} {'n':>2} {'A':>7} {'B':>7} {'A−B':>8}  {'95% CI of Δ':>19} "
        f"{'p':>9}  {'> noise':>7}  {'noise':>6}"
    )
    for row in cmp["metrics"]:
        n = row.get("n_paired", 0)
        if row["diff"] is None or n < _PAIR:
            note = "n=1, no CI" if n == 1 else "n/a"
            mean_a = "n/a" if row["mean_a"] is None else f"{row['mean_a']:.3f}"
            mean_b = "n/a" if row["mean_b"] is None else f"{row['mean_b']:.3f}"
            diff = "n/a" if row["diff"] is None else f"{row['diff']:+.3f}"
            print(
                f"{row['label']:<30} {n:>2} {mean_a:>7} {mean_b:>7} {diff:>8}  "
                f"{note:>19} {'n/a':>9}  {'n/a':>7}  {'n/a':>6}"
            )
            continue
        ci = f"[{row['lo']:+.3f}, {row['hi']:+.3f}]"
        p = f"<= {floor_of_one:.4f}" if row["p"] <= floor_of_one + 1e-12 else f"{row['p']:.4f}"
        nf = "n/a" if row["noise_floor"] is None else f"{row['noise_floor']:.3f}"
        flag = (
            "n/a"
            if row["exceeds_noise_floor"] is None
            else ("yes" if row["exceeds_noise_floor"] else "NO")
        )
        print(
            f"{row['label']:<30} {n:>2} {row['mean_a']:>7.3f} {row['mean_b']:>7.3f} "
            f"{row['diff']:>+8.3f}  {ci:>19} {p:>9}  {flag:>7}  {nf:>6}"
        )
    print("─" * 104)
    print(
        f"  n = paired cases the metric is defined on (τ is n/a below "
        f"{MIN_TAU_STOPS} shared stops, so it pairs on fewer cases)."
    )
    print(f"  reference noise floor: {cmp['reference_noise_floor']}")
    print("  a delta smaller than the noise floor is a re-run of the same pipeline,")
    print("  not a change; and a p-value on a handful of cases is a bug detector,")
    print("  not evidence of anything.")
    print("  the CI is a percentile interval, so with a handful of cases a resample")
    print("  landing exactly on 0 can leave the CI touching 0 while p counts it as")
    print("  ≤ 0: where the two disagree, believe p.")
    print("═" * 104)
    print()


def write_compare_report(cmp: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "compare.json"
    path.write_text(json.dumps(cmp, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  compare report → {path}")
    return path

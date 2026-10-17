"""The printed tables and the JSON/Markdown reports."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core import constants as agent_constants
from quality.bench.analysis import (
    MATCH_RADIUS_KM,
    MIN_TAU_STOPS,
    _get,
    _mean,
    _spread,
    _std,
    bootstrap_ci,
    leg_cap_km,
    resample_indices,
)
from quality.bench.config import (
    _PAIR,
    BOOTSTRAP_ALPHA,
    BOOTSTRAP_SAMPLES,
    BOOTSTRAP_SEED,
    DEFAULT_GRADE,
    DUPLICATE_NAME_RADIUS_M,
    DUPLICATE_RADIUS_M,
    DUPLICATE_SIM,
    DUPLICATE_SIM_RADIUS_M,
    GATED_FAILURE_KINDS,
    GRADE_WEIGHTS,
    REFERENCE_NOISE_FLOOR,
    REPORT_SCHEMA,
    STAGE1_PROXY_RADIUS_M,
    _r,
)
from quality.bench.models import EvaluationResult, ReportInput, RunRecord


def _attr(runs: list[EvaluationResult], path: str) -> list[float]:
    """Defined values of `path` across runs."""
    out: list[float] = []
    for r in runs:
        if r.hard_failure:
            continue
        v = _get(r, path)
        if v is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v))
    return out


def _ms(runs: list[EvaluationResult], path: str, decimals: int = 3) -> str:
    """`mean ± spread` for one metric, or `n/a` when no run defined it."""
    vals = _attr(runs, path)
    if not vals:
        return "n/a"
    m = _mean(vals)
    s = _spread(vals)
    if m is None or s is None:
        return "n/a"
    return f"{m:.{decimals}f}±{s:.{decimals}f}"


def _fit_cell(runs: list[EvaluationResult]) -> str:
    n = sum(1 for r in runs if not r.hard_failure)
    n_fit = sum(1 for r in runs if r.budget_fit and not r.hard_failure)
    return f"{n_fit}/{n}" if n else "n/a"


def case_value(runs: list[EvaluationResult], path: str) -> float | None:
    """One case's score for a metric: the mean over its defined runs."""
    vals = _attr(runs, path)
    return _mean(vals)


CI_METRICS: tuple[tuple[str, str, int], ...] = (
    ("recall_at_k", "Rec@K (750 m greedy)", 3),
    ("stage.recall", "stage-1 pool proxy recall (250 m)", 3),
    ("stage.route_recall_given_pool", "stage-2 recall | stage-1 hit", 3),
    ("precision", "precision", 3),
    ("kendall_tau", "τ shared (vs reference walk)", 3),
    ("detour_km", "detour km", 3),
    ("walk_diff_min", "Δwalk min", 1),
    ("leg.max_leg_km_straight", "max leg km (straight)", 2),
    ("latency_s", "latency s", 2),
)

COMPARE_METRICS: tuple[tuple[str, str], ...] = (
    ("recall_at_k", "Rec@K"),
    ("stage.recall", "stage-1 pool proxy recall"),
    ("stage.route_recall_given_pool", "stage-2 recall | stage-1 hit"),
    ("precision", "precision"),
    ("kendall_tau", "τ shared"),
    ("detour_km", "detour km"),
    ("walk_diff_min", "Δwalk min"),
    ("leg.max_leg_km_straight", "max leg km (straight)"),
)


def per_case_scores(groups: list[list[RunRecord]]) -> dict[str, dict[str, float | None]]:
    """One score per case per metric: the mean over that case's defined runs."""
    out: dict[str, dict[str, float | None]] = {}
    for g in groups:
        if not g:
            continue
        results = [r.result for r in g]
        out[g[0].case] = {path: case_value(results, path) for path, _label, _d in CI_METRICS}
    return out


def overall_stats(
    per_case: dict[str, dict[str, float | None]],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = BOOTSTRAP_ALPHA,
) -> dict[str, dict[str, Any]]:
    """Bootstrap over CASES for every metric in CI_METRICS."""
    out: dict[str, dict[str, Any]] = {}
    for path, label, _d in CI_METRICS:
        values = [v[path] for v in per_case.values() if v.get(path) is not None]
        stats = (
            bootstrap_ci(values, resample_indices(len(values), samples, seed), alpha)
            if values
            else {"mean": None, "lo": None, "hi": None, "n": 0}
        )
        stats["label"] = label
        out[path] = stats
    return out


def noise_floor(groups: list[list[RunRecord]]) -> dict[str, dict[str, Any]]:
    """Mean within-case spread across repeats — the LLM-sampling noise floor."""
    out: dict[str, dict[str, Any]] = {}
    for path, label, _d in CI_METRICS:
        spreads, stds, n_cases = [], [], 0
        for g in groups:
            vals = _attr([r.result for r in g], path)
            if len(vals) < _PAIR:
                continue
            n_cases += 1
            spreads.append(_spread(vals) or 0.0)
            stds.append(_std(vals) or 0.0)
        out[path] = {
            "label": label,
            "mean_within_case_spread": _mean(spreads),
            "mean_within_case_std": _mean(stds),
            "n_cases_with_repeats": n_cases,
        }
    return out


def failure_summary(groups: list[list[RunRecord]]) -> dict[str, Any]:
    """Hard failures and leg-sanity defects — counted, never averaged in."""
    by_kind: dict[str, int] = {}
    gated: list[dict[str, Any]] = []
    defects: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    legitimate: list[dict[str, Any]] = []
    total_runs = 0
    for g in groups:
        for rec in g:
            total_runs += 1
            for f in rec.result.failures:
                by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
                row = {"case": rec.case, "repeat": rec.repeat, **f.as_dict()}
                (gated if f.gated else defects).append(row)
                details.append(row)
            for p in rec.result.leg.duplicate_pairs:
                if p["excluded"]:
                    legitimate.append(
                        {
                            "case": rec.case,
                            "repeat": rec.repeat,
                            "names": p["names"],
                            "dist_m": p["dist_m"],
                            "rule": p["rule"],
                            "reason": p["exception_reason"],
                        }
                    )
    return {
        "total_runs": total_runs,
        "n_failed_runs": len(gated),
        "gated_runs": len(gated),
        "by_kind": by_kind,
        "leg_sanity_defects": len(defects),
        "legitimate_adjacent": legitimate,
        "gated": gated,
        "details": details,
    }


def _f(v: float | None, decimals: int = 3) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}"


def _pad(s: str, w: int) -> str:
    return s.ljust(w)


def _ci_cell(stats: dict[str, Any], decimals: int = 3) -> str:
    if stats.get("mean") is None:
        return "n/a"
    if stats.get("n", 0) < _PAIR:
        return f"{stats['mean']:.{decimals}f} (n=1, no CI)"
    return f"{stats['mean']:.{decimals}f} [{stats['lo']:.{decimals}f}, {stats['hi']:.{decimals}f}]"


def _stage2_is_trivial(overall: dict[str, dict[str, Any]]) -> bool:
    """True when stage-2 recall is 1.0 for want of a pool, not for want of skill."""
    stats = overall.get("stage.route_recall_given_pool", {})
    return stats.get("mean") is not None and stats["mean"] >= 1.0


COLS = [
    "name",
    "s1",
    "s2",
    "recall",
    "prec",
    "tau",
    "detour",
    "walk_diff",
    "fit",
    "dup",
    "unr",
    "lat",
    "hf",
]

COL_TITLES = {
    "name": "Маршрут",
    "s1": "S1 pool",
    "s2": "S2 | S1",
    "recall": "Rec@K",
    "prec": "Prec",
    "tau": "τ shared",
    "detour": "Detour km",
    "walk_diff": "ΔWalk",
    "fit": "Fit",
    "dup": "Dup",
    "unr": "Unr",
    "lat": "Lat s",
    "hf": "HF",
}

COL_WIDTH = {
    "name": 30,
    "s1": 11,
    "s2": 11,
    "recall": 12,
    "prec": 11,
    "tau": 11,
    "detour": 13,
    "walk_diff": 12,
    "fit": 5,
    "dup": 5,
    "unr": 5,
    "lat": 11,
    "hf": 4,
}

COL_PATH = {
    "s1": "stage.recall",
    "s2": "stage.route_recall_given_pool",
    "recall": "recall_at_k",
    "prec": "precision",
    "tau": "kendall_tau",
    "detour": "detour_km",
    "walk_diff": "walk_diff_min",
    "lat": "latency_s",
}


def print_table(groups: list[list[RunRecord]]) -> None:
    header = "│".join(" " + _pad(COL_TITLES[k], COL_WIDTH[k]) + " " for k in COLS)
    total_width = len(header) + 1
    repeat = len(groups[0]) if groups else 1

    print()
    print(f"{'':─^{total_width}}")
    print(f"│{header}│")
    print(f"{'':─^{total_width}}")

    for runs in groups:
        results = [r.result for r in runs]
        r0 = results[0]
        name = (r0.golden.name[: COL_WIDTH["name"]]).ljust(COL_WIDTH["name"])
        cells = {k: _ms(results, COL_PATH[k], 2) for k in COL_PATH}
        cells["fit"] = _fit_cell(results)
        cells["dup"] = str(sum(r.leg.n_duplicate_stops for r in results))
        cells["unr"] = str(sum(r.leg.n_unreachable_legs for r in results))
        cells["hf"] = str(sum(1 for r in results if r.hard_failure))
        row = " │ ".join(cells[k].rjust(COL_WIDTH[k]) if k != "name" else name for k in COLS)
        errs = [r.api_error for r in results if r.api_error]
        print(f"│ {row} │")
        for err in errs:
            width = sum(COL_WIDTH[k] for k in COLS[1:]) + 3 * (len(COLS) - 2)
            print(f"│ {'':{COL_WIDTH['name']}} │{'':{width}}│ {err[:70]} │")

    print(f"{'':─^{total_width}}")
    print()
    print(
        f"  S1 pool = stage-1 retrieval proxy (any returned stop within "
        f"{STAGE1_PROXY_RADIUS_M:.0f} m of a reference stop)"
    )
    print("  S2 | S1 = stage-2 recall conditional on a stage-1 hit")
    print("  Dup = duplicate-POI pairs, Unr = unreachable hops (UNREACHABLE_S),")
    print(f"  HF = gated hard failures. mean±spread over {repeat} repeat(s).")
    print()


def print_ci_table(
    overall: dict[str, dict[str, Any]],
    per_case: dict[str, dict[str, Any]],
    noise: dict[str, dict[str, Any]],
    samples: int,
    seed: int,
) -> None:
    print(f"Bootstrap over cases — mean [95 % CI], B={samples}, seed={seed}")
    print(f"{'metric':<38} {'mean [lo, hi]':<34} {'n':>3}  noise floor")
    print(f"{'─' * 38} {'─' * 34} {'─' * 3}  {'─' * 14}")
    for path, _label, dec in CI_METRICS:
        stats = overall.get(path, {})
        nf = noise.get(path, {}).get("mean_within_case_spread")
        cell = _ci_cell(stats, dec)
        print(
            f"{stats.get('label', path):<38} {cell:<34} {stats.get('n', 0):>3}"
            f"  {('n/a' if nf is None else f'{nf:.{dec}f}')}"
        )
    print()
    print(
        f"  noise floor = mean within-case spread across repeats; reference "
        f"measurement: {REFERENCE_NOISE_FLOOR}"
    )
    print(f"  {len(per_case)} case(s) — with n that small the CI is the finding.")
    if _stage2_is_trivial(overall):
        print("  ⚠ stage-2 recall reads 1.000 BY CONSTRUCTION while the pool fallback is the")
        print("    route's own stops: the route cannot miss a stop stage 1 found. It becomes")
        print("    a measurement only when the API exposes a candidate pool larger than the route.")
    print()


def print_failures(summary: dict[str, Any], overall: dict[str, Any] | None = None) -> None:
    """Hard failures + leg-sanity defects. Deliberately outside every score."""
    print("─" * 78)
    print(
        f"HARD FAILURES (not part of any score above) — {summary['n_failed_runs']}"
        f" of {summary['total_runs']} run(s) gated out of the means"
    )
    if not summary["by_kind"]:
        print("  none")
    for kind, count in sorted(summary["by_kind"].items()):
        gated = "gated" if kind in GATED_FAILURE_KINDS else "counted"
        print(f"  {kind:<20} {count:>3}   ({gated})")
    for row in summary["gated"]:
        print(f"    ✗ {row['case']}#{row['repeat']}: {row['detail']}")
    defects = [d for d in summary["details"] if not d["gated"]]
    print(
        f"  leg-sanity defects kept in the means: {len(defects)} "
        "(hiding them by dropping the run would defeat the check)"
    )
    for row in defects:
        print(f"    ! {row['case']}#{row['repeat']}: {row['detail']}")
    legitimate = summary.get("legitimate_adjacent") or []
    if legitimate:
        print(
            f"  legitimate neighbours the rule would flag — declared two objects, "
            f"not counted: {len(legitimate)}"
        )
        for row in legitimate:
            print(
                f"    ~ {row['case']}#{row['repeat']}: {row['names'][0]} ~ "
                f"{row['names'][1]} ({row['dist_m']:.0f} m, {row['rule']})"
            )
    if overall:
        rec = overall.get("recall_at_k", {})
        print(f"  headline (for reference only): Rec@K {_ci_cell(rec)}")
    print()


def _agg(runs: list[EvaluationResult], path: str) -> dict[str, Any]:
    """mean / spread / min / max of one metric over the runs of a case."""
    vals = _attr(runs, path)
    if not vals:
        return {"mean": None, "spread": None, "min": None, "max": None, "n": 0}
    return {
        "mean": round(_mean(vals), 4),
        "spread": round(_spread(vals), 4),
        "std": round(_std(vals), 4) if _std(vals) is not None else None,
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
        "n": len(vals),
    }


def write_json_report(
    groups: list[list[RunRecord]],
    out_dir: Path,
    report: ReportInput,
    *,
    per_case: dict[str, Any],
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.json"
    results = []
    for runs in groups:
        eval_runs = [r.result for r in runs]
        r0 = eval_runs[0]
        errs = [r.api_error for r in eval_runs if r.api_error]
        results.append(
            {
                "case": runs[0].case,
                "route": r0.golden.name,
                "source": r0.golden.source,
                "query_ru": r0.golden.query_ru,
                "repeat": len(runs),
                "recall_at_k": _agg(eval_runs, "recall_at_k"),
                "precision": _agg(eval_runs, "precision"),
                "kendall_tau": _agg(eval_runs, "kendall_tau"),
                "tau_shared_stops": _agg(eval_runs, "shared_stops"),
                "detour_km": _agg(eval_runs, "detour_km"),
                "our_walk_km": _agg(eval_runs, "our_walk_km"),
                "our_walk_km_net": _agg(eval_runs, "our_walk_km_net"),
                "ref_walk_km": _agg(eval_runs, "ref_walk_km"),
                "walk_diff_min": _agg(eval_runs, "walk_diff_min"),
                "budget_fit": _fit_cell(eval_runs),
                "latency_s": _agg(eval_runs, "latency_s"),
                "case_scores": per_case.get(runs[0].case, {}),
                "stage_split": asdict(r0.stage),
                "leg_sanity": asdict(r0.leg),
                "failures": [f.as_dict() for f in r0.failures],
                "api_error": errs[0] if errs else None,
                "runs": [
                    {
                        "repeat": rec.repeat,
                        "ts": rec.ts,
                        "http_status": rec.result.http_status,
                        "intent_source": rec.result.intent_source,
                        "candidate_ids_source": rec.result.candidate_ids_source,
                        "recall_at_k": round(rec.result.recall_at_k, 4),
                        "precision": round(rec.result.precision, 4),
                        "kendall_tau": (
                            round(rec.result.kendall_tau, 4)
                            if rec.result.kendall_tau is not None
                            else None
                        ),
                        "shared_stops": rec.result.shared_stops,
                        "stage1_pool_recall": _r(rec.result.stage.recall),
                        "stage2_recall_given_pool": _r(rec.result.stage.route_recall_given_pool),
                        "detour_km": _r(rec.result.detour_km),
                        "our_walk_km": _r(rec.result.our_walk_km),
                        "ref_walk_km": _r(rec.result.ref_walk_km),
                        "walk_diff_min": _r(rec.result.walk_diff_min, 2),
                        "budget_fit": rec.result.budget_fit,
                        "latency_s": round(rec.result.latency_s, 3),
                        "hard_failure": rec.result.hard_failure,
                        "failures": [f.as_dict() for f in rec.result.failures],
                        "leg_sanity": asdict(rec.result.leg),
                        "our_stops": [
                            {
                                "id": s.place_id,
                                "name": s.name,
                                "lat": s.lat,
                                "lon": s.lon,
                                "visit_minutes": s.visit_minutes,
                            }
                            for s in rec.result.our_stops
                        ],
                    }
                    for rec in runs
                ],
            }
        )

    report = {
        "schema": REPORT_SCHEMA,
        "mode": report.mode,
        "cases_dir": report.cases_dir,
        "generated_at": report.generated_at,
        "n_routes": len(groups),
        "repeat": len(groups[0]) if groups else 0,
        "n_runs": sum(len(g) for g in groups),
        "snapshot": report.snapshot,
        "bootstrap": {
            "samples": report.samples,
            "seed": report.seed,
            "alpha": BOOTSTRAP_ALPHA,
            "resample": "cases",
        },
        "metric_notes": {
            "kendall_tau": (
                "tau-b, our stop order vs the reference walk order, restricted "
                f"to the stops shared by both (n/a below {MIN_TAU_STOPS} shared stops). "
                "The reference walk is the shortest open path over the reference "
                "stops' own coordinates, NOT the order the stops appear in the "
                "Wikivoyage-derived .json file."
            ),
            "detour_km": (
                "our walk length minus the reference walk length, both measured "
                "as haversine sums so the two sides are comparable."
            ),
            "spread": "half-range (max - min) / 2 over the runs of a route",
            "stage_split": (
                "The API does not expose the pre-rerank candidate pool, and the "
                "benchmark has no database access offline, so stage-1 is a PROXY: "
                "for each graded reference stop, is any returned stop within "
                f"{STAGE1_PROXY_RADIUS_M:.0f} m of it. That is an upper bound on "
                "pool recall. stage-2 recall is conditional on a stage-1 hit."
            ),
            "grades": (
                f"grade weights {GRADE_WEIGHTS}; an ungraded stop counts as '{DEFAULT_GRADE}'."
            ),
            "max_leg_km_straight": (
                "the longest straight-line hop between the returned stops. The wire "
                "carries no per-leg figure any more (the old debug.trace.max_leg_seconds "
                "went with the pipeline), so this is the offline leg-cap check, and the "
                "bound is the engine's own rule read from routing.optimize.max_leg_seconds "
                "at the case's own profile and budget — a quarter of the budget up to "
                f"{float(agent_constants.MAX_WALK_LEG_KM):g} km on foot or "
                f"{float(agent_constants.BICYCLE_LEG_KM):g} km by bicycle, and the budget "
                f"itself for anything motorised (a four-hour fallback, {leg_cap_km('auto'):g} km, "
                "when the request names none). The straight line is a lower bound on the "
                "real leg, not the leg itself."
            ),
            "n_unreachable_legs": (
                "NOT MEASURABLE from the wire: the new response carries no cost matrix "
                "and no UNREACHABLE_S sentinel, so this stays 0 rather than being guessed."
            ),
            "duplicates": (
                f"same POI twice inside one route: <{DUPLICATE_RADIUS_M:.0f} m "
                f"(any name), identical normalised name <{DUPLICATE_NAME_RADIUS_M:.0f} m, "
                f"or name token overlap >= {DUPLICATE_SIM} <{DUPLICATE_SIM_RADIUS_M:.0f} m. "
                "A pair listed in quality/cases/duplicate_exceptions.json is still "
                "reported but marked `excluded` and is not counted — the rule fired, the "
                "pair is declared two objects (see leg_sanity.duplicate_pairs)."
            ),
            "hard_failures": (
                f"gated kinds {sorted(GATED_FAILURE_KINDS)} are dropped from every "
                "quality mean and reported outside every score; leg-sanity defects "
                "(duplicate_stop, leg_over_cap) stay in the means and are counted."
            ),
        },
        "per_case_scores": per_case,
        "overall": _r(report.overall, 4),
        "noise_floor": {
            "computed": _r(report.noise, 4),
            "reference_measurement": REFERENCE_NOISE_FLOOR,
        },
        "hard_failures": report.failures,
        "metrics_drift_vs_snapshot": report.drift_rows,
        "results": results,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  JSON report → {path}")
    return path


def write_metrics_jsonl(
    groups: list[list[RunRecord]],
    out_dir: Path,
    overall: dict[str, Any],
    failures: dict[str, Any],
) -> Path:
    """One line per case — the shape a CI job diffs between runs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.metrics.jsonl"
    lines = []
    for runs in groups:
        eval_runs = [r.result for r in runs]
        case = runs[0].case
        lines.append(
            json.dumps(
                {
                    "case": case,
                    "name": eval_runs[0].golden.name,
                    "n_runs": len(runs),
                    "scores": {
                        path_: _r(case_value(eval_runs, path_), 4) for path_, _l, _d in CI_METRICS
                    },
                    "hard_failures": [f.as_dict() for f in eval_runs[0].failures],
                    "n_duplicate_stops": sum(r.leg.n_duplicate_stops for r in eval_runs),
                    "n_unreachable_legs": sum(r.leg.n_unreachable_legs for r in eval_runs),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    lines.append(
        json.dumps(
            {
                "case": "__overall__",
                "mean_and_ci95": {
                    path_: _r(overall.get(path_, {}), 4) for path_, _l, _d in CI_METRICS
                },
                "n_hard_failure_runs": failures["n_failed_runs"],
                "n_leg_sanity_defects": failures["leg_sanity_defects"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  metrics JSONL → {path}")
    return path


def write_md_report(groups: list[list[RunRecord]], out_dir: Path, report: ReportInput) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.md"
    repeat = len(groups[0]) if groups else 0
    lines: list[str] = [
        "# Benchmark Report: Grodno Route Planner",
        "",
        f"**Mode:** `{report.mode}` · **Generated:** {report.generated_at}",
        "",
        (
            f"**Runs per case:** {repeat} · **cases:** {len(groups)} · "
            f"**runs:** {sum(len(g) for g in groups)}"
        ),
        "",
    ]
    if report.snapshot:
        lines += [
            (
                f"**Snapshot:** `{report.snapshot.get('dir', '—')}` · "
                f"{report.snapshot.get('n_rows', 0)} row(s) · "
                f"git `{report.snapshot.get('git_sha', '—')}`"
            ),
            "",
        ]
    if report.drift_rows:
        lines += [
            (
                f"> ⚠ {len(report.drift_rows)} row(s) recomputed to metrics that differ from "
                "the values recorded in the snapshot — the metric code changed since "
                "the run."
            ),
            "",
        ]

    lines += [
        "## Per-case scores (`mean±spread` over repeats)",
        "",
        (
            "| case | S1 pool | S2 \\| S1 | Rec@K | prec | τ | detour km | Δwalk | fit "
            "| dup | unr | HF |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for runs in groups:
        eval_runs = [r.result for r in runs]
        lines.append(
            f"| {runs[0].case} | {_ms(eval_runs, 'stage.recall', 2)} | "
            f"{_ms(eval_runs, 'stage.route_recall_given_pool', 2)} | "
            f"{_ms(eval_runs, 'recall_at_k', 2)} | "
            f"{_ms(eval_runs, 'precision', 2)} | "
            f"{_ms(eval_runs, 'kendall_tau', 2)} | "
            f"{_ms(eval_runs, 'detour_km', 3)} | "
            f"{_ms(eval_runs, 'walk_diff_min', 1)} | {_fit_cell(eval_runs)} | "
            f"{sum(r.leg.n_duplicate_stops for r in eval_runs)} | "
            f"{sum(r.leg.n_unreachable_legs for r in eval_runs)} | "
            f"{sum(1 for r in eval_runs if r.hard_failure)} |"
        )

    lines += [
        "",
        f"## Overall — bootstrap over cases (B={report.samples}, seed={report.seed})",
        "",
        "| metric | mean [95 % CI] | n cases | noise floor (within-case spread) |",
        "|---|---|---|---|",
    ]
    for path_, _label, dec in CI_METRICS:
        stats = report.overall.get(path_, {})
        nf = report.noise.get(path_, {}).get("mean_within_case_spread")
        lines.append(
            f"| {stats.get('label', path_)} | {_ci_cell(stats, dec)} | "
            f"{stats.get('n', 0)} | "
            f"{'n/a' if nf is None else f'{nf:.{dec}f}'} |"
        )
    lines += [
        "",
        f"Reference noise floor: {REFERENCE_NOISE_FLOOR}.",
        "",
        "## Hard failures — outside every score above",
        "",
        (
            f"**{report.failures['n_failed_runs']} of {report.failures['total_runs']} run(s) gated "
            f"out of the quality means.** Counts by kind:"
        ),
        "",
    ]
    if not report.failures["by_kind"]:
        lines.append("- none")
    for kind, count in sorted(report.failures["by_kind"].items()):
        tag = "gated" if kind in GATED_FAILURE_KINDS else "counted, kept in means"
        lines.append(f"- `{kind}`: {count} ({tag})")
    if report.failures["gated"]:
        lines += ["", "| case | repeat | kind | detail |", "|---|---|---|---|"]
        for row in report.failures["gated"]:
            lines.append(f"| {row['case']} | {row['repeat']} | {row['kind']} | {row['detail']} |")
    lines += [
        "",
        f"Leg-sanity defects kept in the means: {report.failures['leg_sanity_defects']}.",
        "",
        "## How the stage split is measured",
        "",
        "The API does not expose the pre-rerank candidate pool: `debug` carries",
        "`intent_source`, `constraints` and the validate `trace`, and the pool",
        "`retrieve()` builds never reaches the response. The benchmark also has no",
        "database access offline, so even a list of ids could not be scored against",
        "reference coordinates. Stage-1 is therefore a **proxy**: for each graded",
        f"reference stop, is any returned stop within {STAGE1_PROXY_RADIUS_M:.0f} m of it",
        "(an upper bound on pool recall — a place the retriever never saw cannot",
        "produce a returned stop next to it). The 250 m radius is deliberately much",
        f"tighter than the {MATCH_RADIUS_KM * 1000:.0f} m matcher used for recall@K, which",
        "over-merges distinct nearby churches.",
        "",
        "Stage-2 is the assembly quality *given* that: the conditional recall over the",
        "reference stops the proxy found, plus precision, τ, detour, leg sanity and",
        "budget fit — all of which the assembly decides.",
        "",
        "> While the pool fallback is the route's own stops, stage-2 recall reads",
        "> 1.000 **by construction** — the route cannot miss a stop stage 1 found,",
        "> because stage 1 only looked at the route. It becomes a measurement the",
        "> moment the API exposes a candidate pool larger than the route; until then",
        "> read the other stage-2 numbers (precision, τ, detour, leg sanity), which",
        "> do discriminate.",
        "",
        "Grades: `"
        + ", ".join(f"{k}={v}" for k, v in GRADE_WEIGHTS.items())
        + f"`, ungraded treated as `{DEFAULT_GRADE}`.",
        "",
        "## Determinism",
        "",
        "`--replay` recomputes every metric from the recorded rows with no agent, no",
        "Valhalla and no clock: two replays of the same snapshot are byte-identical,",
        "which is what makes a metric change reviewable as a diff rather than a claim.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Markdown report → {path}")
    return path

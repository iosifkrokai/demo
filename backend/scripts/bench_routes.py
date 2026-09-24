#!/usr/bin/env python3
"""
bench_routes.py — Golden-set evaluation for the Grodno route planner.

Анти-утечка: эталоны не попадают в seed.
Референсные маршруты (backend/benchmarks/routes/*.json) основаны на Wikivoyage
и содержат реальные координаты, которые могут отличаться от базы.
Все метрики считаются по фактическому API-ответу без обращения к seed-данным.

Запуск (из backend/):
    .venv/bin/python -m scripts.bench_routes [--base-url http://localhost:8080] [--report-dir backend/benchmarks]

Метрики:
    recall@K      — доля эталонных стопов, покрытых нашим маршрутом (K ≤ 8, т.к. ROUTE_MAX_STOPS=8)
    precision     — доля наших стопов, присутствующих в эталоне
    kendall_tau   — τ на пересечении порядков (ручная реализация, без scipy)
    walk_diff     — (our_walk − est_walk) в минутах
    budget_fit    — bool, укладывается ли маршрут в эталонный бюджет
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ── пути ────────────────────────────────────────────────────────────────────

BACKEND = Path(__file__).parent.parent.resolve()
BENCH_ROUTES = BACKEND / "benchmarks" / "routes"
BENCH_OUT = BACKEND / "benchmarks"


# ── модели ──────────────────────────────────────────────────────────────────

@dataclass
class GoldenStop:
    name: str
    lat: float
    lon: float
    visit_minutes: int | None = None


@dataclass
class GoldenRoute:
    name: str
    source: str
    query_ru: str
    budget_minutes: int
    stops: list[GoldenStop]
    est_walk_minutes: int | None = None
    path: Path = field(default=None)


@dataclass
class OurStop:
    name: str
    lat: float
    lon: float
    visit_minutes: int | None = None


@dataclass
class EvaluationResult:
    golden: GoldenRoute
    our_stops: list[OurStop] = field(default_factory=list)
    recall_at_k: float = 0.0
    precision: float = 0.0
    kendall_tau: float = 0.0
    walk_diff_min: float | None = None  # ours − est_walk
    budget_fit: bool = False
    api_error: str | None = None
    latency_s: float = 0.0


# ── geo helpers ─────────────────────────────────────────────────────────────

# Earth radius in km (Haversine)
_R = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance between two lat/lon points in km."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _R * math.asin(math.sqrt(a))


# ── matching ────────────────────────────────────────────────────────────────

# Two stops are considered the same if they are within this radius (km)
MATCH_RADIUS_KM = 0.75  # ~750 m — generous for a pedestrian context

# recall@K: max number of stops we could ever return
MAX_K = 8  # mirrors ROUTE_MAX_STOPS in constants.py


def match_golden_to_ours(
    golden_stops: list[GoldenStop],
    our_stops: list[OurStop],
) -> list[tuple[int | None, int | None]]:
    """Greedy best-cost match between golden stops and our stops.

    Returns a list aligned with golden_stops where each entry is
    (golden_idx, our_idx | None) — None means no match within MATCH_RADIUS_KM.
    """
    n = len(golden_stops)
    m = len(our_stops)
    if n == 0 or m == 0:
        return [(None, None)] * n

    # Precompute distance matrix
    dists: list[list[float]] = [
        [haversine_km(gs.lat, gs.lon, os.lat, os.lon) for os in our_stops]
        for gs in golden_stops
    ]

    # Greedy: for each golden stop, pick the closest our stop within radius
    used_our: set[int] = set()
    result: list[tuple[int | None, int | None]] = []
    for gi in range(n):
        best_mi: int | None = None
        best_d = float("inf")
        for mi in range(m):
            if mi in used_our:
                continue
            d = dists[gi][mi]
            if d < best_d:
                best_d = d
                best_mi = mi
        if best_mi is not None and best_d <= MATCH_RADIUS_KM:
            used_our.add(best_mi)
            result.append((gi, best_mi))
        else:
            result.append((gi, None))
    return result


# ── Kendall tau (manual, no scipy) ──────────────────────────────────────────

def kendall_tau(order_a: list[int], order_b: list[int]) -> float:
    """Kendall's tau-b on the shared elements of two orderings.

    order_a and order_b are permutations of the same element set (but may
    omit some elements).  Returns tau in [-1, 1].
    0.0 is returned when there are fewer than 2 comparable pairs.

    tau-b = (P - Q) / sqrt((P + Q + Ty) * (P + Q + Tx))
    P = concordant pairs, Q = discordant, Tx/Ty = ties only in each ordering.
    """
    if len(order_a) < 2 or len(order_b) < 2:
        return 0.0

    # Map element → position in order_a
    pos_a: dict[int, int] = {e: i for i, e in enumerate(order_a)}
    # Filter order_b to elements present in order_a
    shared = [e for e in order_b if e in pos_a]
    n = len(shared)
    if n < 2:
        return 0.0

    # Rank of each shared element in order_b
    ranks_b = [i for i, e in enumerate(order_b) if e in pos_a]

    # P / Q count over pairs of shared elements
    P = Q = 0
    for i in range(n):
        for j in range(i + 1, n):
            rank_a_i = pos_a[shared[i]]
            rank_a_j = pos_a[shared[j]]
            rank_b_i = ranks_b[i]
            rank_b_j = ranks_b[j]
            diff_a = rank_a_i - rank_a_j
            diff_b = rank_b_i - rank_b_j
            prod = diff_a * diff_b
            if prod > 0:
                P += 1
            elif prod < 0:
                Q += 1

    # Ties within order_a and order_b (both have the same shared elements)
    Tx = sum(1 for i in range(n) for j in range(i + 1, n)
             if pos_a[shared[i]] == pos_a[shared[j]])  # always 0 for strict permutations
    Ty = sum(1 for i in range(n) for j in range(i + 1, n)
             if ranks_b[i] == ranks_b[j])  # always 0

    denom = math.sqrt((P + Q + Tx) * (P + Q + Ty))
    if denom == 0:
        return 0.0
    return (P - Q) / denom


# ── API call ─────────────────────────────────────────────────────────────────

def call_generate(base_url: str, query: str, budget_minutes: int | None, origin_lat: float | None, origin_lon: float | None) -> dict:
    """POST /routes/generate → parsed dict (raw JSON)."""
    payload = {
        "query": query,
        "time_budget_minutes": budget_minutes,
    }
    if origin_lat is not None and origin_lon is not None:
        payload["origin"] = {"lat": origin_lat, "lon": origin_lon}

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/routes/generate",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── evaluation ──────────────────────────────────────────────────────────────

def evaluate(golden: GoldenRoute, base_url: str) -> EvaluationResult:
    result = EvaluationResult(golden=golden)

    # Estimate origin from centroid of golden stops (reasonable proxy)
    centroid_lat = sum(s.lat for s in golden.stops) / len(golden.stops)
    centroid_lon = sum(s.lon for s in golden.stops) / len(golden.stops)

    t0 = time.monotonic()
    try:
        raw = call_generate(
            base_url,
            golden.query_ru,
            golden.budget_minutes,
            centroid_lat,
            centroid_lon,
        )
    except urllib.error.URLError as exc:
        result.api_error = str(exc)
        result.latency_s = time.monotonic() - t0
        return result
    result.latency_s = time.monotonic() - t0

    # Parse response
    points = raw.get("points") or []
    budget_info = raw.get("budget") or {}
    summary = raw.get("summary") or {}

    result.our_stops = [
        OurStop(
            name=p.get("name", ""),
            lat=p.get("lat", 0.0),
            lon=p.get("lon", 0.0),
            visit_minutes=p.get("visit_minutes"),
        )
        for p in points
    ]

    # ── recall@K ──────────────────────────────────────────────────────────
    matches = match_golden_to_ours(golden.stops, result.our_stops)
    covered = sum(1 for _, our_i in matches if our_i is not None)
    k = min(len(golden.stops), MAX_K)
    result.recall_at_k = covered / k if k > 0 else 0.0

    # ── precision ─────────────────────────────────────────────────────────
    golden_covered = {gi for gi, _ in matches if gi is not None}
    n_ours_in_golden = sum(1 for _, our_i in matches if our_i is not None)
    n_ours_total = len(result.our_stops)
    result.precision = n_ours_in_golden / n_ours_total if n_ours_total > 0 else 0.0

    # ── Kendall tau on intersection ────────────────────────────────────────
    # Our matched stops in golden order → indices in our list
    # golden order = order of golden stops (0..n-1)
    our_order_a = [our_i for _, our_i in matches if our_i is not None]
    # our order = natural order of our_stops
    # For tau we need a shared element set. Use integer IDs = position in our_stops.
    # order_a = golden order of matched our indices
    # order_b = our natural order of matched our indices
    order_b = sorted(our_order_a)  # natural order
    result.kendall_tau = kendall_tau(our_order_a, order_b)

    # ── walk_minutes diff ───────────────────────────────────────────────────
    walk_s = summary.get("time_seconds") or budget_info.get("walk_minutes", 0) * 60
    walk_min = walk_s / 60.0
    if golden.est_walk_minutes is not None:
        result.walk_diff_min = walk_min - golden.est_walk_minutes
    elif budget_info.get("walk_minutes"):
        result.walk_diff_min = walk_min - budget_info.get("walk_minutes")

    # ── budget fit ─────────────────────────────────────────────────────────
    result.budget_fit = budget_info.get("fits", False)

    return result


# ── formatting helpers ───────────────────────────────────────────────────────

def _f(v: float | None, decimals: int = 3) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}"


def _pad(s: str, w: int) -> str:
    return s.ljust(w)


# ── ASCII table ─────────────────────────────────────────────────────────────

def print_table(results: list[EvaluationResult]) -> None:
    col = {
        "name": "Маршрут",
        "recall": "Rec@K",
        "prec": "Prec",
        "tau": "τ",
        "walk_diff": "ΔWalk",
        "fit": "Fit",
        "lat": "Lat s",
        "err": "Ошибка",
    }
    w = {
        "name": 38,
        "recall": 7,
        "prec": 7,
        "tau": 7,
        "walk_diff": 8,
        "fit": 5,
        "lat": 8,
        "err": 20,
    }

    header = "│".join(
        " " + _pad(col[k], w[k]) + " "
        for k in ["name", "recall", "prec", "tau", "walk_diff", "fit", "lat"]
    )
    sep = "─" * (len(header) + 1)
    total_width = len(header) + 1  # +1 for the leading separator char

    print()
    print(f"{'':─^{total_width}}")
    print(f"│{header}│")
    print(f"{'':─^{total_width}}")

    for r in results:
        name = (r.golden.name[: w["name"]]).ljust(w["name"])
        recall = _f(r.recall_at_k, 2)
        prec = _f(r.precision, 2)
        tau = _f(r.kendall_tau)
        walk_diff = _f(r.walk_diff_min, 1) if r.walk_diff_min is not None else "—"
        fit = "✓" if r.budget_fit else "✗"
        lat = f"{r.latency_s:.2f}s"
        err = r.api_error or ""

        if err:
            print(f"│ {name} │ {recall:>5} │ {prec:>5} │ {tau:>5} │ {walk_diff:>7} │ {fit:>3} │ {lat:>7} │ {err[:w['err']]:<{w['err']}} │")
        else:
            print(f"│ {name} │ {recall:>5} │ {prec:>5} │ {tau:>5} │ {walk_diff:>7} │ {fit:>3} │ {lat:>7} │")

    print(f"{'':─^{total_width}}")

    # summary line
    n = len(results)
    if n > 0:
        avg_recall = sum(r.recall_at_k for r in results) / n
        avg_prec = sum(r.precision for r in results) / n
        avg_tau = sum(r.kendall_tau for r in results) / n
        avg_walk = [r.walk_diff_min for r in results if r.walk_diff_min is not None]
        avg_wd = sum(avg_walk) / len(avg_walk) if avg_walk else None
        n_fit = sum(1 for r in results if r.budget_fit)
        print()
        print(f"  avg recall@K = {avg_recall:.3f}   avg precision = {avg_prec:.3f}   "
              f"avg τ = {avg_tau:.3f}   "
              f"avg Δwalk = {_f(avg_wd, 1)} min   fit = {n_fit}/{n}")
    print()


# ── report writers ───────────────────────────────────────────────────────────

def write_json_report(results: list[EvaluationResult], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.json"
    rows = []
    for r in results:
        rows.append({
            "route": r.golden.name,
            "source": r.golden.source,
            "query_ru": r.golden.query_ru,
            "recall_at_k": round(r.recall_at_k, 4),
            "precision": round(r.precision, 4),
            "kendall_tau": round(r.kendall_tau, 4),
            "walk_diff_min": (round(r.walk_diff_min, 2) if r.walk_diff_min is not None else None),
            "budget_fit": r.budget_fit,
            "latency_s": round(r.latency_s, 3),
            "api_error": r.api_error,
            "our_stops": [
                {"name": s.name, "lat": s.lat, "lon": s.lon, "visit_minutes": s.visit_minutes}
                for s in r.our_stops
            ],
        })
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_routes": len(results),
        "results": rows,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  JSON report → {path}")


def write_md_report(results: list[EvaluationResult], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.md"
    lines = [
        "# Benchmark Report: Grodno Route Planner",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Metrics summary",
        "",
        "| Маршрут | Rec@K | Prec | τ | ΔWalk (min) | Fit | Latency |",
        "|---------|-------|------|---|------------|-----|---------|",
    ]
    for r in results:
        wd = f"{r.walk_diff_min:+.1f}" if r.walk_diff_min is not None else "—"
        err = f"⚠ {r.api_error[:40]}" if r.api_error else ""
        lines.append(
            f"| {r.golden.name} | {r.recall_at_k:.3f} | {r.precision:.3f} | "
            f"{r.kendall_tau:.3f} | {wd} | "
            f"{'✓' if r.budget_fit else '✗'} | {r.latency_s:.2f}s {err} |"
        )

    n = len(results)
    if n > 0:
        avg_recall = sum(r.recall_at_k for r in results) / n
        avg_prec = sum(r.precision for r in results) / n
        avg_tau = sum(r.kendall_tau for r in results) / n
        avg_walk = [r.walk_diff_min for r in results if r.walk_diff_min is not None]
        avg_wd = sum(avg_walk) / len(avg_walk) if avg_walk else None
        n_fit = sum(1 for r in results if r.budget_fit)
        lines += [
            "",
            f"**Average:** Rec@K={avg_recall:.3f} · Prec={avg_prec:.3f} · "
            f"τ={avg_tau:.3f} · ΔWalk={avg_wd:+.1f} min · Fit={n_fit}/{n}",
        ]
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Markdown report → {path}")


# ── loader ──────────────────────────────────────────────────────────────────

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
            )
            for s in data.get("stops", [])
        ]
        routes.append(GoldenRoute(
            name=data["name"],
            source=data.get("source", ""),
            query_ru=data["query_ru"],
            budget_minutes=data.get("budget_minutes", 120),
            stops=stops,
            est_walk_minutes=data.get("est_walk_minutes"),
            path=p,
        ))
    return routes


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Golden-set route benchmark runner.",
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
        default=str(BACKEND / "benchmarks"),
        help="Directory for report.json / report.md (default: backend/benchmarks)",
    )
    args = parser.parse_args()

    routes = load_golden_routes(BENCH_ROUTES)
    if not routes:
        print(f"No benchmark files found in {BENCH_ROUTES}", file=sys.stderr)
        sys.exit(1)

    print(f"Benchmark runner — {len(routes)} route(s) found")
    print(f"API base URL: {args.base_url}")
    print(f"Reports will be written to: {args.report_dir}")

    results: list[EvaluationResult] = []
    for route in routes:
        print(f"\n▶ {route.name}")
        print(f"  query : {route.query_ru}")
        print(f"  source: {route.source}")
        result = evaluate(route, args.base_url)
        results.append(result)

        if result.api_error:
            print(f"  ✗ API error: {result.api_error}")
        else:
            print(f"  our_stops    : {len(result.our_stops)}")
            print(f"  recall@K     : {result.recall_at_k:.3f}")
            print(f"  precision    : {result.precision:.3f}")
            print(f"  Kendall τ    : {result.kendall_tau:.4f}")
            print(f"  Δwalk        : {_f(result.walk_diff_min, 1)} min")
            print(f"  budget_fit   : {'✓' if result.budget_fit else '✗'}")
            print(f"  latency      : {result.latency_s:.2f}s")

    print_table(results)
    write_json_report(results, Path(args.report_dir))
    write_md_report(results, Path(args.report_dir))


if __name__ == "__main__":
    main()

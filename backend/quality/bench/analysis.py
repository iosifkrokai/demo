"""Geometry, scoring, bootstrap statistics and snapshot I/O."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import subprocess
import urllib.error
import urllib.request
from datetime import UTC, datetime
from functools import lru_cache
from http import HTTPStatus
from itertools import pairwise
from pathlib import Path
from typing import Any

from core import constants as agent_constants
from quality.bench.config import (
    _PAIR,
    BACKEND,
    BENCH_SNAPSHOTS,
    BOOTSTRAP_ALPHA,
    DUPLICATE_EXCEPTIONS,
    DUPLICATE_NAME_RADIUS_M,
    DUPLICATE_RADIUS_M,
    DUPLICATE_SIM,
    DUPLICATE_SIM_RADIUS_M,
    LEG_SPEED_KMH,
    REPORT_SCHEMA,
    REQUEST_TIMEOUT_S,
    ROW_SCHEMA,
    SNAPSHOT_SCHEMA,
    STAGE1_PROXY_RADIUS_M,
    UNREACHABLE_S,
    _is_num,
)
from quality.bench.models import (
    _R,
    EvaluationResult,
    Failure,
    GoldenRoute,
    GoldenStop,
    LegSanity,
    OurStop,
    ReferenceWalk,
    RunRecord,
    StageSplit,
)
from routing.optimize import max_leg_seconds
from routing.valhalla.constants import costing_for


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance between two lat/lon points in km."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _R * math.asin(math.sqrt(a))


def norm_name(name: str) -> str:
    """Lowercase, drop parentheticals and punctuation — the agent's own rule."""
    n = re.sub(r"\([^)]*\)", " ", (name or "").lower())
    n = re.sub(r"[^0-9a-zа-яё]+", " ", n)
    return " ".join(n.split())


def name_similarity(a: str, b: str) -> float:
    """Jaccard similarity of the normalised token sets of two POI names."""
    ta = set(norm_name(a).split())
    tb = set(norm_name(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


MATCH_RADIUS_KM = 0.75


def match_golden_to_ours(
    golden_stops: list[GoldenStop],
    our_stops: list[OurStop],
) -> list[tuple[int | None, int | None]]:
    """Greedy best-cost match between golden stops and our stops."""
    n = len(golden_stops)
    m = len(our_stops)
    if n == 0 or m == 0:
        return [(None, None)] * n

    dists: list[list[float]] = [
        [haversine_km(gs.lat, gs.lon, os.lat, os.lon) for os in our_stops] for gs in golden_stops
    ]

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


REF_WALK_EXACT_MAX_STOPS = 12

MIN_TAU_STOPS = 3


def _pairwise_km(points: list[tuple[float, float]]) -> list[list[float]]:
    return [[haversine_km(a[0], a[1], b[0], b[1]) for b in points] for a in points]


def _nearest_neighbour_order(d: list[list[float]]) -> list[int]:
    """Greedy shortest open walk from stop 0 (used when the exact DP is skipped)."""
    n = len(d)
    order = [0]
    remaining = set(range(1, n))
    while remaining:
        last = order[-1]
        nxt = min(sorted(remaining), key=lambda j: d[last][j])
        order.append(nxt)
        remaining.discard(nxt)
    return order


def shortest_walk_order(points: list[tuple[float, float]]) -> list[int]:
    """Indices of `points` in the order of a shortest open walk through all of them."""
    n = len(points)
    if n <= 1:
        return list(range(n))

    d = _pairwise_km(points)
    if n > REF_WALK_EXACT_MAX_STOPS:
        return _nearest_neighbour_order(d)

    best: dict[tuple[int, int], tuple[float, tuple[int, ...]]] = {}
    for i in range(n):
        best[(1 << i, i)] = (0.0, (i,))

    for mask in range(1, 1 << n):
        for last in range(n):
            cur = best.get((mask, last))
            if cur is None:
                continue
            for nxt in range(n):
                if mask >> nxt & 1:
                    continue
                key = (mask | (1 << nxt), nxt)
                cand = (cur[0] + d[last][nxt], cur[1] + (nxt,))
                prev = best.get(key)
                if prev is None or cand < prev:
                    best[key] = cand

    full = (1 << n) - 1
    winner = min(
        (v for (mask, _last), v in best.items() if mask == full),
        key=lambda v: (round(v[0], 9), v[1]),
    )
    return list(winner[1])


def walk_distance_km(points: list[tuple[float, float]], order: list[int]) -> float:
    """Haversine length of walking `points` in `order` (open walk, no return leg)."""
    return sum(
        haversine_km(*points[order[i]], *points[order[i + 1]]) for i in range(len(order) - 1)
    )


def max_leg_km(points: list[tuple[float, float]]) -> float | None:
    """Longest straight-line hop of an open walk, or None for < 2 points."""
    if len(points) < _PAIR:
        return None
    return max(haversine_km(*a, *b) for a, b in pairwise(points))


def build_reference_walk(stops: list[GoldenStop]) -> ReferenceWalk:
    """The reference walk: shortest order over the reference stops, plus its length."""
    points = [(s.lat, s.lon) for s in stops]
    order = shortest_walk_order(points)
    return ReferenceWalk(
        order=order,
        distance_km=walk_distance_km(points, order),
        article_distance_km=walk_distance_km(points, list(range(len(stops)))),
    )


def kendall_tau(order_a: list[int], order_b: list[int]) -> float | None:
    """Kendall's tau-b between two orderings, over the elements they share."""
    pos_a = {e: i for i, e in enumerate(order_a)}
    shared = [e for e in order_b if e in pos_a]
    n = len(shared)
    if n < MIN_TAU_STOPS:
        return None

    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            prod = (pos_a[shared[i]] - pos_a[shared[j]]) * (i - j)
            if prod > 0:
                concordant += 1
            elif prod < 0:
                discordant += 1

    denom = concordant + discordant
    if denom == 0:
        return None
    return (concordant - discordant) / denom


def detour_km(our_distance_km: float | None, ref_distance_km: float | None) -> float | None:
    """How much longer our walk is than the reference walk, in km."""
    if our_distance_km is None or ref_distance_km is None:
        return None
    return our_distance_km - ref_distance_km


def _any_within(
    targets: list[GoldenStop],
    points: list[tuple[float, float]],
    radius_m: float,
) -> list[bool]:
    """Per target: is ANY returned stop within `radius_m`?"""
    radius_km = radius_m / 1000.0
    out: list[bool] = []
    for gs in targets:
        out.append(any(haversine_km(gs.lat, gs.lon, lat, lon) <= radius_km for lat, lon in points))
    return out


def stage_split(
    golden: GoldenRoute,
    our_stops: list[OurStop],
) -> StageSplit:
    """Split one run's outcome into a retrieval proxy and an assembly number."""
    points = [(s.lat, s.lon) for s in our_stops]
    source = "route_points"
    exposed = False

    covered = _any_within(golden.stops, points, STAGE1_PROXY_RADIUS_M)
    n_cov = sum(covered)
    total_w = golden.total_weight
    cov_w = sum(s.weight for s, c in zip(golden.stops, covered, strict=True) if c)
    recall = (n_cov / len(golden.stops)) if golden.stops else 0.0
    recall_w = (cov_w / total_w) if total_w > 0 else 0.0

    in_route = 0
    for gi, ok in enumerate(covered):
        if ok and result_matches_stop(golden, our_stops, gi):
            in_route += 1
    given_pool = (in_route / n_cov) if n_cov else None

    return StageSplit(
        pool_exposed=exposed,
        pool_source=source,
        pool_size=len(points),
        proxy_radius_m=STAGE1_PROXY_RADIUS_M,
        n_reference=len(golden.stops),
        n_reference_weighted=total_w,
        n_covered=n_cov,
        n_covered_weighted=cov_w,
        recall=recall,
        recall_weighted=recall_w,
        route_recall_given_pool=given_pool,
        n_pool_covered_in_route=in_route,
    )


def result_matches_stop(golden: GoldenRoute, our_stops: list[OurStop], golden_idx: int) -> bool:
    """Is reference stop `golden_idx` actually in the returned route?"""
    gs = golden.stops[golden_idx]
    return any(haversine_km(gs.lat, gs.lon, o.lat, o.lon) <= MATCH_RADIUS_KM for o in our_stops)


@lru_cache(maxsize=4)
def _load_duplicate_exceptions(path_str: str) -> dict[frozenset[str], dict[str, Any]]:
    """Read the hand-authored legitimate-neighbour list, keyed by normalised name pair."""
    path = Path(path_str)
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[frozenset[str], dict[str, Any]] = {}
    for entry in data.get("pairs", []):
        names = entry.get("names") or []
        try:
            norm_a, norm_b = (norm_name(n) for n in names)
        except ValueError as exc:
            raise ValueError(
                f"{path}: an exception needs exactly two names, got {names!r}"
            ) from exc
        if not norm_a or not norm_b or norm_a == norm_b:
            raise ValueError(
                f"{path}: exception names must normalise to two distinct names: {names!r}"
            )
        key = frozenset({norm_a, norm_b})
        if key in out:
            raise ValueError(f"{path}: duplicate exception for {names!r}")
        out[key] = entry
    return out


def duplicate_exceptions(path: Path | None = None) -> dict[frozenset[str], dict[str, Any]]:
    """The declared legitimate neighbours the duplicate rule consults."""
    return _load_duplicate_exceptions(str(path or DUPLICATE_EXCEPTIONS))


def find_duplicate_stop_pairs(our_stops: list[OurStop]) -> list[dict[str, Any]]:
    """Pairs of returned stops the rule flags, marked when declared legitimate."""
    exceptions = duplicate_exceptions()
    pairs: list[dict[str, Any]] = []
    for i in range(len(our_stops)):
        for j in range(i + 1, len(our_stops)):
            a, b = our_stops[i], our_stops[j]
            dist_m = haversine_km(a.lat, a.lon, b.lat, b.lon) * 1000.0
            rule = _duplicate_rule(a.name, b.name, dist_m)
            if rule is None:
                continue
            entry = exceptions.get(frozenset((norm_name(a.name), norm_name(b.name))))
            pairs.append(
                {
                    "our_stop_indices": [i, j],
                    "names": [a.name, b.name],
                    "place_ids": [a.place_id, b.place_id],
                    "dist_m": round(dist_m, 1),
                    "name_similarity": round(name_similarity(a.name, b.name), 3),
                    "rule": rule,
                    "excluded": entry is not None,
                    "exception_reason": entry.get("reason") if entry else None,
                }
            )
    return pairs


def _duplicate_rule(name_a: str, name_b: str, dist_m: float) -> str | None:
    if dist_m < DUPLICATE_RADIUS_M:
        return "coincident"
    if (
        norm_name(name_a)
        and norm_name(name_a) == norm_name(name_b)
        and dist_m < DUPLICATE_NAME_RADIUS_M
    ):
        return "same_name"
    if name_similarity(name_a, name_b) >= DUPLICATE_SIM and dist_m < DUPLICATE_SIM_RADIUS_M:
        return "same_name_near"
    return None


def leg_cap_km(profile: str | None, budget_minutes: int | None = None) -> float:
    """The longest single leg the engine's own rule allows, in km, at this budget.

    `routing.optimize.max_leg_seconds` states that rule in seconds and in terms of the
    budget — a quarter of it capped at 5 km on foot or 20 km by bicycle, and the whole
    budget for anything motorised (a four-hour fallback when the request names none) —
    so the bound is read from there rather than restated, and converted at the speed
    the engine prices that transport with.
    """
    costing = costing_for(str(profile or "").strip().lower())
    budget_s = int(budget_minutes) * 60 if budget_minutes else None
    speed = LEG_SPEED_KMH.get(costing, float(agent_constants.AUTO_SPEED_KMH))
    return max_leg_seconds(budget_s, costing) / 3600.0 * speed


def leg_sanity(
    our_stops: list[OurStop],
    summary: dict[str, Any],
    raw: dict[str, Any],
    profile: str | None = None,
    budget_minutes: int | None = None,
) -> LegSanity:
    """Is the returned route physically plausible, judging from the wire alone?"""
    leg = LegSanity(n_stops=len(our_stops))
    points = [(s.lat, s.lon) for s in our_stops]
    leg.max_leg_km_straight = max_leg_km(points)

    length_km = summary.get("length_km")
    time_s = summary.get("time_seconds")
    if _is_num(length_km) and _is_num(time_s) and length_km > 0 and time_s > 0:
        leg.avg_speed_kmh = float(length_km) / (float(time_s) / 3600.0)

    leg.cap_km = leg_cap_km(profile, budget_minutes)
    if leg.max_leg_km_straight is not None and leg.max_leg_km_straight > leg.cap_km:
        leg.over_cap = True

    leg.duplicate_pairs = find_duplicate_stop_pairs(our_stops)
    leg.n_duplicate_stops = sum(1 for p in leg.duplicate_pairs if not p["excluded"])
    leg.n_legitimate_adjacent = sum(1 for p in leg.duplicate_pairs if p["excluded"])
    leg.geometry_missing = "shape" in (raw or {}) and not raw.get("shape")
    return leg


def build_request(
    query: str,
    budget_minutes: int | None,
    origin_lat: float | None = None,
    origin_lon: float | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """The exact body sent to POST /routes/generate (also what a row records)."""
    payload: dict[str, Any] = {
        "query": query,
        "time_budget_minutes": budget_minutes,
    }
    if origin_lat is not None and origin_lon is not None:
        payload["origin"] = {"lat": origin_lat, "lon": origin_lon}
    if profile:
        payload["profile"] = profile
    return payload


def post_generate(base_url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST /routes/generate → (http_status, parsed JSON)."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/routes/generate",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        return int(resp.status), json.loads(resp.read().decode("utf-8"))


def route_origin(golden: GoldenRoute) -> tuple[float | None, float | None]:
    """Origin estimate: the case's own start, or the centroid of the reference stops."""
    if golden.origin is not None:
        return golden.origin
    if not golden.stops:
        return None, None
    lat = sum(s.lat for s in golden.stops) / len(golden.stops)
    lon = sum(s.lon for s in golden.stops) / len(golden.stops)
    return lat, lon


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace")
        return json.loads(body).get("detail") or body
    except Exception:
        return f"HTTP {exc.code}"


def score_response(
    golden: GoldenRoute,
    raw: dict[str, Any] | None,
    *,
    http_status: int | None = None,
    api_error: str | None = None,
    latency_s: float = 0.0,
) -> EvaluationResult:
    """Every metric for one run, from a response and nothing else."""
    result = EvaluationResult(
        golden=golden, api_error=api_error, latency_s=latency_s, http_status=http_status
    )
    if api_error is not None or (http_status is not None and http_status >= HTTPStatus.BAD_REQUEST):
        result.failures.append(
            Failure(
                kind="api_error" if api_error else "http_status",
                detail=api_error or f"HTTP {http_status}",
            )
        )
        return result

    result.our_stops = _our_stops(raw or {})
    result.n_stops = len(result.our_stops)
    if not result.our_stops:
        result.failures.append(Failure(kind="zero_stops", detail="route has no stops"))

    summary = (raw or {}).get("summary") or {}
    budget_info = (raw or {}).get("budget") or {}
    interpretation = (raw or {}).get("interpretation") or {}
    result.intent_source = interpretation.get("source") or ((raw or {}).get("debug") or {}).get(
        "requirements_source"
    )
    result.stops_dropped = int(budget_info.get("stops_dropped") or 0)

    result.candidate_ids = [s.place_id for s in result.our_stops if s.place_id is not None]
    result.candidate_ids_source = "route_points"

    _score_coverage(result)
    _score_reference(result)
    _score_budget(result, summary, budget_info)
    result.stage = stage_split(golden, result.our_stops)
    result.leg = leg_sanity(
        result.our_stops, summary, raw or {}, golden.profile, golden.budget_minutes
    )
    result.failures.extend(_leg_failures(result.leg, golden.profile))
    return result


def _our_stops(raw: dict[str, Any]) -> list[OurStop]:
    out: list[OurStop] = []
    for p in raw.get("points") or []:
        out.append(
            OurStop(
                name=p.get("name", ""),
                lat=float(p.get("lat") or 0.0),
                lon=float(p.get("lon") or 0.0),
                visit_minutes=p.get("visit_minutes"),
                place_id=p.get("id") if isinstance(p.get("id"), int) else None,
            )
        )
    return out


def _score_coverage(result: EvaluationResult) -> None:
    """recall@K, precision and the match table (the 750 m greedy matcher)."""
    golden = result.golden
    matches = match_golden_to_ours(golden.stops, result.our_stops)
    result.matches = matches
    covered = sum(1 for _, our_i in matches if our_i is not None)
    k = len(golden.stops)
    result.recall_at_k = min(covered / k, 1.0) if k > 0 else 0.0
    n_ours = len(result.our_stops)
    result.precision = covered / n_ours if n_ours > 0 else 0.0


def _score_reference(result: EvaluationResult) -> None:
    """Reference walk, tau over the shared stops, walk lengths and detour."""
    golden = result.golden
    ref = build_reference_walk(golden.stops)
    result.ref_walk_km = ref.distance_km

    ref_shared = [result.matches[gi][1] for gi in ref.order if result.matches[gi][1] is not None]
    result.shared_stops = len(ref_shared)
    result.kendall_tau = kendall_tau(ref_shared, sorted(ref_shared))

    our_points = [(s.lat, s.lon) for s in result.our_stops]
    result.our_walk_km = (
        walk_distance_km(our_points, list(range(len(our_points)))) if our_points else None
    )
    result.detour_km = detour_km(result.our_walk_km, ref.distance_km)


def _score_budget(
    result: EvaluationResult, summary: dict[str, Any], budget_info: dict[str, Any]
) -> None:
    walk_s = summary.get("time_seconds") or budget_info.get("walk_minutes", 0) * 60
    walk_min = walk_s / 60.0
    if result.golden.est_walk_minutes is not None:
        result.walk_diff_min = walk_min - result.golden.est_walk_minutes
    elif budget_info.get("walk_minutes"):
        result.walk_diff_min = walk_min - budget_info.get("walk_minutes")
    result.our_walk_km_net = summary.get("length_km")
    result.budget_fit = bool(budget_info.get("fits", False))


def _leg_failures(leg: LegSanity, profile: str | None = None) -> list[Failure]:
    out: list[Failure] = []
    if leg.unreachable_sentinel:
        out.append(
            Failure(
                kind="unreachable_leg",
                detail=(
                    f"Valhalla sentinel {UNREACHABLE_S:.0f} in the trace: >= 1 hop is "
                    "not connectable (the matrix is not shipped, so the count is a "
                    "lower bound)"
                ),
            )
        )
    if leg.geometry_missing:
        out.append(Failure(kind="geometry_missing", detail="response carried an empty shape"))
    duplicate_pairs = [p for p in leg.duplicate_pairs if not p["excluded"]]
    if duplicate_pairs:
        names = "; ".join(
            f"{p['names'][0]} ~ {p['names'][1]} ({p['dist_m']:.0f} m, {p['rule']})"
            for p in duplicate_pairs
        )
        out.append(
            Failure(
                kind="duplicate_stop", detail=f"{len(duplicate_pairs)} duplicate pair(s): {names}"
            )
        )
    if leg.over_cap:
        out.append(
            Failure(
                kind="leg_over_cap",
                detail=f"a leg of {leg.max_leg_km_straight:.1f} km exceeds the engine's cap of "
                f"{leg.cap_km:.1f} km for {costing_for(profile)}",
            )
        )
    return out


def percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile of an already sorted list (clamped)."""
    if not sorted_values:
        return float("nan")
    k = min(len(sorted_values) - 1, max(0, int(q * len(sorted_values))))
    return sorted_values[k]


def resample_indices(n: int, samples: int, seed: int) -> list[tuple[int, ...]]:
    """`samples` bootstrap resamples of `n` case indices, from a seeded RNG."""
    key = (n, samples, seed)
    cached = _RESAMPLE_CACHE.get(key)
    if cached is None:
        rng = random.Random(seed)
        cached = [tuple(rng.randrange(n) for _ in range(n)) for _ in range(samples)]
        _RESAMPLE_CACHE[key] = cached
    return cached


_RESAMPLE_CACHE: dict[tuple[int, int, int], list[tuple[int, ...]]] = {}


def bootstrap_ci(
    values: list[float],
    indices: list[tuple[int, ...]],
    alpha: float = BOOTSTRAP_ALPHA,
) -> dict[str, Any]:
    """Mean and percentile CI of the mean of `values` under `indices`."""
    n = len(values)
    if n == 0:
        return {"mean": None, "lo": None, "hi": None, "n": 0}
    mean = sum(values) / n
    if n == 1:
        return {"mean": mean, "lo": mean, "hi": mean, "n": 1}
    means = sorted(sum(values[i] for i in idx) / n for idx in indices)
    return {
        "mean": mean,
        "lo": percentile(means, alpha / 2),
        "hi": percentile(means, 1 - alpha / 2),
        "n": n,
    }


def paired_bootstrap(
    a_values: list[float],
    b_values: list[float],
    indices: list[tuple[int, ...]],
    alpha: float = BOOTSTRAP_ALPHA,
) -> dict[str, Any]:
    """Paired bootstrap on the difference A − B over the shared cases."""
    n = len(a_values)
    if n != len(b_values) or n == 0:
        return {"diff": None, "lo": None, "hi": None, "p": None, "n": n}
    diff = sum(a_values) / n - sum(b_values) / n
    if n == 1:
        return {"diff": diff, "lo": None, "hi": None, "p": None, "n": 1}
    diffs = [
        sum(a_values[i] for i in idx) / n - sum(b_values[i] for i in idx) / n for idx in indices
    ]
    ordered = sorted(diffs)
    le = sum(1 for d in diffs if d <= 0.0)
    ge = sum(1 for d in diffs if d >= 0.0)
    m = len(diffs)
    p = min(1.0, 2.0 * min(le, ge) / m)
    return {
        "diff": diff,
        "lo": percentile(ordered, alpha / 2),
        "hi": percentile(ordered, 1 - alpha / 2),
        "p": max(p, 1.0 / m),
        "n": n,
    }


def default_snapshot_dir() -> Path:
    return BENCH_SNAPSHOTS / datetime.now(UTC).strftime("%Y-%m-%d")


def _git_sha() -> str:
    """Short HEAD sha, or "unknown" outside a checkout (never fatal)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=BACKEND.parent,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.strip() or "unknown"


def _run_id() -> str:
    """Unique-per-invocation id, so a snapshot dir is never silently mixed."""
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def golden_sha256(golden: GoldenRoute) -> str | None:
    if not golden.path or not golden.path.exists():
        return None
    cached = _GOLDEN_SHA_CACHE.get(golden.case)
    if cached is None:
        text = golden.path.read_text(encoding="utf-8") + (golden.fingerprint or "")
        cached = _sha256_text(text)
        _GOLDEN_SHA_CACHE[golden.case] = cached
    return cached


_GOLDEN_SHA_CACHE: dict[str, str] = {}


def make_meta(
    cases: list[GoldenRoute],
    *,
    base_url: str,
    repeat: int,
    started_at: str,
    run_id: str,
    git_sha: str,
) -> dict[str, Any]:
    return {
        "record": "meta",
        "schema": SNAPSHOT_SCHEMA,
        "run_id": run_id,
        "started_at": started_at,
        "git_sha": git_sha,
        "base_url": base_url,
        "repeat": repeat,
        "cases": [c.case for c in cases],
        "golden_sha256": {c.case: golden_sha256(c) for c in cases},
        "bench_version": REPORT_SCHEMA,
    }


def make_row(
    record: RunRecord,
    base_url: str,
) -> dict[str, Any]:
    """The JSONL row for one (case, repeat) — the unit replay reads."""
    r = record.result
    return {
        "record": "row",
        "schema": ROW_SCHEMA,
        "case": record.case,
        "case_name": record.case_name,
        "repeat": record.repeat,
        "ts": record.ts,
        "git_sha": record.git_sha,
        "base_url": base_url,
        "golden_sha256": golden_sha256(r.golden),
        "request": record.request,
        "http_status": r.http_status,
        "api_error": r.api_error,
        "candidate_ids": r.candidate_ids,
        "candidate_ids_source": r.candidate_ids_source,
        "response": record.response,
        "metrics": r.metrics_dict(),
    }


def write_meta(meta: dict[str, Any], snapshot_dir: Path, append: bool) -> Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / "rows.jsonl"
    if not append:
        path.write_text("", encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
    return path


def append_row(row: dict[str, Any], path: Path) -> None:
    """Append one row. A crashed run therefore leaves a replayable partial file."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_snapshot(snapshot_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read a snapshot dir → (meta, rows)."""
    files = sorted(snapshot_dir.glob("rows*.jsonl"))
    if not files:
        raise FileNotFoundError(
            f"no rows*.jsonl snapshot file in {snapshot_dir} "
            "(a snapshot dir holds rows.jsonl plus the reports)"
        )
    meta: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for path in files:
        with path.open(encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                kind = rec.get("record")
                if kind == "meta":
                    meta = meta or rec
                elif kind == "row":
                    rows.append(rec)
    if not rows:
        raise ValueError(f"{snapshot_dir} holds no row records")
    rows.sort(key=lambda r: (str(r.get("case")), int(r.get("repeat", 0))))
    return meta, rows


def _get(obj, path: str):
    """Read a (possibly dotted) attribute path off a run result."""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        cur = getattr(cur, part, None)
    return cur


def _spread(values: list[float]) -> float | None:
    if not values:
        return None
    return (max(values) - min(values)) / 2.0


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _std(values: list[float]) -> float | None:
    """Sample std (ddof=1); None for a single value."""
    if len(values) < _PAIR:
        return None
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)

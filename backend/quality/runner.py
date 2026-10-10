#!/usr/bin/env python3
"""
bench_routes.py — Golden-set evaluation for the Grodno route planner.

Modes: LIVE, REPLAY, COMPARE, GOLDEN — one metric implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

BACKEND = Path(__file__).parent.parent.resolve()
QUALITY = BACKEND / "quality"
BENCH_ROUTES = QUALITY / "cases" / "routes"
BENCH_GOLDEN = QUALITY / "cases" / "compliance"
BENCH_OUT = QUALITY / "reports"
BENCH_SNAPSHOTS = BENCH_OUT / "snapshots"

STATUSES = frozenset(
    {"ready", "catalogue", "degraded", "pending", "infeasible", "rejected",
     "needs_clarification", "error"}
)

CHECK_API_ERROR = "api_error"
CHECK_WRONG_STATUS = "wrong_status"
CHECK_TOO_FEW_PLACES = "too_few_places"
CHECK_MISSING_MANDATORY_CATEGORY = "missing_mandatory_category"
CHECK_FORBIDDEN_CATEGORY_PRESENT = "forbidden_category_present"
CHECK_MISSING_NAMED_PLACE = "missing_named_place"
CHECK_OUT_OF_REGION_POINT = "out_of_region_point"
CHECK_OVER_BUDGET = "over_budget"
CHECK_RESULT_MODE = "result_mode_mismatch"
CHECK_PARITY = "ru_en_parity_mismatch"

CHECK_PRIORITY: tuple[str, ...] = (
    CHECK_API_ERROR,
    CHECK_WRONG_STATUS,
    CHECK_TOO_FEW_PLACES,
    CHECK_MISSING_MANDATORY_CATEGORY,
    CHECK_FORBIDDEN_CATEGORY_PRESENT,
    CHECK_MISSING_NAMED_PLACE,
    CHECK_OUT_OF_REGION_POINT,
    CHECK_OVER_BUDGET,
    CHECK_RESULT_MODE,
)

CANONICAL_CATEGORIES: dict[str, str] = {}
CANONICAL_CODES_NORM: frozenset[str] = frozenset()


if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import constants as agent_constants  # noqa: E402
from reference.geofence import inside_project_area  # noqa: E402

CANONICAL_CATEGORIES = {
    canonical.strip().lower().replace("ё", "е"): canonical
    for canonical in agent_constants.CATEGORIES
}
CANONICAL_CODES_NORM = frozenset(CANONICAL_CATEGORIES)


UNREACHABLE_S = float(agent_constants.UNREACHABLE_S)
MAX_WALK_LEG_KM = float(agent_constants.MAX_WALK_LEG_KM)
DUPLICATE_RADIUS_M = float(agent_constants.DUPLICATE_RADIUS_M)
DUPLICATE_NAME_RADIUS_M = float(agent_constants.DUPLICATE_NAME_RADIUS_M)
GRODNO_BBOX = dict(agent_constants.GRODNO_BBOX)

STAGE1_PROXY_RADIUS_M = 250.0

DUPLICATE_SIM = 0.6
DUPLICATE_SIM_RADIUS_M = 1000.0

GRADE_WEIGHTS = {"must-see": 3.0, "nice-to-have": 1.0, "available": 0.0}
DEFAULT_GRADE = "must-see"

GATED_FAILURE_KINDS = frozenset(
    {"api_error", "http_status", "zero_stops", "unreachable_leg", "geometry_missing"}
)

BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 20260926
BOOTSTRAP_ALPHA = 0.05

REQUEST_TIMEOUT_S = 300.0
HEALTH_TIMEOUT_S = 5.0

SNAPSHOT_SCHEMA = "bench_routes/snapshot/1"
ROW_SCHEMA = "bench_routes/row/1"
REPORT_SCHEMA = "bench_routes/report/2"
GOLDEN_SNAPSHOT_FILE = "golden_rows.jsonl"
GOLDEN_SNAPSHOT_SCHEMA = "bench_routes/golden_snapshot/1"
GOLDEN_ROW_SCHEMA = "bench_routes/golden_row/1"

REFERENCE_NOISE_FLOOR = "recall 0.711 ± 0.150 over 9 runs (measured 2026-09-26)"


@dataclass
class GoldenStop:
    name: str
    lat: float
    lon: float
    visit_minutes: int | None = None
    grade: str | None = None

    @property
    def weight(self) -> float:
        return GRADE_WEIGHTS.get(self.grade or DEFAULT_GRADE, 0.0)


@dataclass
class GoldenRoute:
    name: str
    source: str
    query_ru: str
    budget_minutes: int
    stops: list[GoldenStop]
    est_walk_minutes: int | None = None
    path: Path = field(default=None)
    case: str = ""

    @property
    def total_weight(self) -> float:
        return sum(s.weight for s in self.stops)


@dataclass
class OurStop:
    name: str
    lat: float
    lon: float
    visit_minutes: int | None = None
    place_id: int | None = None


@dataclass
class ReferenceWalk:
    """The benchmark's reference walk, derived from the reference stop geometry.

    `order` indexes `GoldenRoute.stops`; `distance_km` is its haversine length.
    """

    order: list[int]
    distance_km: float
    article_distance_km: float


@dataclass
class StageSplit:
    """Retrieval (stage 1) vs assembly (stage 2) for one run.

    `pool_exposed` is False for the current API: the pool proxy is the returned stops.
    """

    pool_exposed: bool
    pool_source: str
    pool_size: int
    proxy_radius_m: float
    n_reference: int
    n_reference_weighted: float
    n_covered: int
    n_covered_weighted: float
    recall: float
    recall_weighted: float
    route_recall_given_pool: float | None
    n_pool_covered_in_route: int


@dataclass
class LegSanity:
    """Whether the returned route is physically walkable at all."""

    n_stops: int = 0
    max_leg_seconds: float | None = None
    avg_speed_kmh: float | None = None
    max_leg_km: float | None = None
    max_leg_km_straight: float | None = None
    over_cap: bool = False
    unreachable_sentinel: bool = False
    n_unreachable_legs: int = 0
    duplicate_pairs: list[dict] = field(default_factory=list)
    n_duplicate_stops: int = 0
    geometry_missing: bool = False


@dataclass
class Failure:
    kind: str
    detail: str

    @property
    def gated(self) -> bool:
        return self.kind in GATED_FAILURE_KINDS

    def as_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail, "gated": self.gated}


@dataclass
class GoldenCase:
    """One requirement-compliance case from quality/cases/compliance/*.json."""

    id: str
    locale: str
    query: str
    filters: dict
    expectations: dict
    parity_group: str | None = None
    path: Path | None = None


@dataclass
class ComplianceVerdict:
    """PASS/FAIL for one golden case (or parity group) against a plan response.

    `reason` is the first failed CHECK_* code or "ok"; unverified checks are marked.
    """

    case_id: str
    passed: bool
    reason: str
    detail: str = ""
    status: str | None = None
    status_source: str | None = None
    checks: dict = field(default_factory=dict)

    @property
    def failed_checks(self) -> list[str]:
        return [k for k, v in self.checks.items() if v.get("ok") is False]

    @property
    def unverified_checks(self) -> list[str]:
        return [k for k, v in self.checks.items() if v.get("unverified")]

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "reason": self.reason,
            "detail": self.detail,
            "status": self.status,
            "status_source": self.status_source,
            "checks": self.checks,
        }


@dataclass
class EvaluationResult:
    golden: GoldenRoute
    our_stops: list[OurStop] = field(default_factory=list)
    recall_at_k: float = 0.0
    precision: float = 0.0
    kendall_tau: float | None = None
    shared_stops: int = 0
    our_walk_km: float | None = None
    our_walk_km_net: float | None = None
    ref_walk_km: float | None = None
    detour_km: float | None = None
    walk_diff_min: float | None = None
    budget_fit: bool = False
    api_error: str | None = None
    latency_s: float = 0.0
    http_status: int | None = None
    intent_source: str | None = None
    stops_dropped: int = 0
    n_stops: int = 0
    matches: list[tuple[int | None, int | None]] = field(default_factory=list)
    stage: StageSplit = field(default_factory=lambda: StageSplit(
        False, "none", 0, STAGE1_PROXY_RADIUS_M, 0, 0.0, 0, 0.0, 0.0, 0.0, None, 0
    ))
    leg: LegSanity = field(default_factory=LegSanity)
    candidate_ids: list[int] = field(default_factory=list)
    candidate_ids_source: str = "none"
    failures: list[Failure] = field(default_factory=list)

    @property
    def hard_failure(self) -> bool:
        return any(f.gated for f in self.failures)

    @property
    def failure_kinds(self) -> list[str]:
        return [f.kind for f in self.failures]

    def metrics_dict(self) -> dict:
        """The metric block recorded in a snapshot row (and diffed on replay)."""
        return {
            "recall_at_k": _r(self.recall_at_k),
            "precision": _r(self.precision),
            "kendall_tau": _r(self.kendall_tau),
            "shared_stops": self.shared_stops,
            "our_walk_km": _r(self.our_walk_km),
            "our_walk_km_net": _r(self.our_walk_km_net),
            "ref_walk_km": _r(self.ref_walk_km),
            "detour_km": _r(self.detour_km),
            "walk_diff_min": _r(self.walk_diff_min, 3),
            "budget_fit": self.budget_fit,
            "stops_dropped": self.stops_dropped,
            "intent_source": self.intent_source,
            "n_stops": self.n_stops,
            "stage": {
                "pool_exposed": self.stage.pool_exposed,
                "pool_source": self.stage.pool_source,
                "pool_size": self.stage.pool_size,
                "proxy_radius_m": self.stage.proxy_radius_m,
                "n_reference": self.stage.n_reference,
                "n_covered": self.stage.n_covered,
                "recall": _r(self.stage.recall),
                "recall_weighted": _r(self.stage.recall_weighted),
                "route_recall_given_pool": _r(self.stage.route_recall_given_pool),
            },
            "leg": {
                "max_leg_seconds": _r(self.leg.max_leg_seconds, 3),
                "max_leg_km": _r(self.leg.max_leg_km),
                "max_leg_km_straight": _r(self.leg.max_leg_km_straight),
                "over_cap": self.leg.over_cap,
                "unreachable_sentinel": self.leg.unreachable_sentinel,
                "n_unreachable_legs": self.leg.n_unreachable_legs,
                "n_duplicate_stops": self.leg.n_duplicate_stops,
                "geometry_missing": self.leg.geometry_missing,
            },
            "candidate_ids_source": self.candidate_ids_source,
            "http_status": self.http_status,
            "api_error": self.api_error,
            "latency_s": _r(self.latency_s, 3),
            "hard_failure": self.hard_failure,
            "failure_kinds": self.failure_kinds,
        }


@dataclass
class RunRecord:
    """One (case, repeat) run — the unit the snapshot stores and the report reads."""

    case: str
    case_name: str
    repeat: int
    result: EvaluationResult
    request: dict | None = None
    response: dict | None = None
    ts: str | None = None
    git_sha: str | None = None
    recorded_metrics: dict | None = None

    @property
    def n_defined(self) -> int:
        return 0 if self.result.api_error else 1


_R = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance between two lat/lon points in km."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _R * math.asin(math.sqrt(a))


def _norm_name(name: str) -> str:
    """Lowercase, drop parentheticals and punctuation — the agent's own rule.

    Mirrors planner/pipeline.py::_norm_name, so both consider the same names identical.
    """
    n = re.sub(r"\([^)]*\)", " ", (name or "").lower())
    n = re.sub(r"[^0-9a-zа-яё]+", " ", n)
    return " ".join(n.split())


def name_similarity(a: str, b: str) -> float:
    """Jaccard similarity of the normalised token sets of two POI names."""
    ta = set(_norm_name(a).split())
    tb = set(_norm_name(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


MATCH_RADIUS_KM = 0.75


def match_golden_to_ours(
    golden_stops: list[GoldenStop],
    our_stops: list[OurStop],
) -> list[tuple[int | None, int | None]]:
    """Greedy best-cost match between golden stops and our stops.

    Per golden stop, (golden_idx, our_idx | None); None = no match within MATCH_RADIUS_KM.
    """
    n = len(golden_stops)
    m = len(our_stops)
    if n == 0 or m == 0:
        return [(None, None)] * n

    dists: list[list[float]] = [
        [haversine_km(gs.lat, gs.lon, os.lat, os.lon) for os in our_stops]
        for gs in golden_stops
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
    return [
        [haversine_km(a[0], a[1], b[0], b[1]) for b in points]
        for a in points
    ]


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
    """Indices of `points` in the order of a shortest open walk through all of them.

    Exact DP up to REF_WALK_EXACT_MAX_STOPS, nearest neighbour above; ties are stable.
    """
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
        haversine_km(*points[order[i]], *points[order[i + 1]])
        for i in range(len(order) - 1)
    )


def max_leg_km(points: list[tuple[float, float]]) -> float | None:
    """Longest straight-line hop of an open walk, or None for < 2 points.

    This is the offline lower bound of the network figure LegSanity.max_leg_km.
    """
    if len(points) < 2:
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
    """Kendall's tau-b between two orderings, over the elements they share.

    Returns None below MIN_TAU_STOPS shared elements, where tau-b carries no information.
    """
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
    """How much longer our walk is than the reference walk, in km.

    Negative is legitimate; None when either side is unknown.
    """
    if our_distance_km is None or ref_distance_km is None:
        return None
    return our_distance_km - ref_distance_km


POOL_PROBE_PATHS: tuple[tuple[str, ...], ...] = (
    ("debug", "trace", "candidates"),
    ("debug", "candidates"),
    ("candidates",),
    ("debug", "trace", "candidate_ids"),
    ("debug", "candidate_ids"),
    ("pool",),
)


def _dig(raw: dict, path: tuple[str, ...]):
    cur = raw
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def probe_stage1_pool(raw: dict) -> dict | None:
    """Find a candidate pool in a response, if the API happens to expose one.

    Returns {"path", "ids", "points"} or None; bare ids cannot be scored offline.
    """
    for path in POOL_PROBE_PATHS:
        value = _dig(raw or {}, path)
        if not isinstance(value, list) or not value:
            continue
        ids: list[int] = []
        points: list[tuple[float, float]] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                ids.append(item["id"])
                if isinstance(item.get("lat"), (int, float)) and isinstance(
                    item.get("lon"), (int, float)
                ):
                    points.append((float(item["lat"]), float(item["lon"])))
            elif isinstance(item, int):
                ids.append(item)
            else:
                return None
        if not ids:
            return None
        return {"path": ".".join(path), "ids": ids, "points": points or None}
    return None


def _any_within(
    targets: list[GoldenStop],
    points: list[tuple[float, float]],
    radius_m: float,
) -> list[bool]:
    """Per target: is ANY returned stop within `radius_m`?

    Not a one-to-one assignment — the question is "was this place reachable at all".
    """
    radius_km = radius_m / 1000.0
    out: list[bool] = []
    for gs in targets:
        out.append(
            any(haversine_km(gs.lat, gs.lon, lat, lon) <= radius_km for lat, lon in points)
        )
    return out


def stage_split(
    golden: GoldenRoute,
    our_stops: list[OurStop],
    pool: dict | None = None,
) -> StageSplit:
    """Split one run's outcome into a retrieval proxy and an assembly number."""
    if pool and pool.get("points"):
        points = pool["points"]
        source = "stage1_pool"
        exposed = True
    else:
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


def result_matches_stop(
    golden: GoldenRoute, our_stops: list[OurStop], golden_idx: int
) -> bool:
    """Is reference stop `golden_idx` actually in the returned route?"""
    gs = golden.stops[golden_idx]
    return any(
        haversine_km(gs.lat, gs.lon, o.lat, o.lon) <= MATCH_RADIUS_KM
        for o in our_stops
    )


def find_duplicate_stop_pairs(our_stops: list[OurStop]) -> list[dict]:
    """Pairs of returned stops that are the same physical POI, in visit order.

    Three rules, first match wins: coincident, same_name, same_name_near.
    """
    pairs: list[dict] = []
    for i in range(len(our_stops)):
        for j in range(i + 1, len(our_stops)):
            a, b = our_stops[i], our_stops[j]
            dist_m = haversine_km(a.lat, a.lon, b.lat, b.lon) * 1000.0
            rule = _duplicate_rule(a.name, b.name, dist_m)
            if rule is None:
                continue
            pairs.append({
                "our_stop_indices": [i, j],
                "names": [a.name, b.name],
                "place_ids": [a.place_id, b.place_id],
                "dist_m": round(dist_m, 1),
                "name_similarity": round(name_similarity(a.name, b.name), 3),
                "rule": rule,
            })
    return pairs


def _duplicate_rule(name_a: str, name_b: str, dist_m: float) -> str | None:
    if dist_m < DUPLICATE_RADIUS_M:
        return "coincident"
    if (
        _norm_name(name_a)
        and _norm_name(name_a) == _norm_name(name_b)
        and dist_m < DUPLICATE_NAME_RADIUS_M
    ):
        return "same_name"
    if (
        name_similarity(name_a, name_b) >= DUPLICATE_SIM
        and dist_m < DUPLICATE_SIM_RADIUS_M
    ):
        return "same_name_near"
    return None


def leg_sanity(
    our_stops: list[OurStop],
    trace: dict,
    summary: dict,
    raw: dict,
) -> LegSanity:
    """Is the returned route physically walkable?

    UNREACHABLE_S (1e9) is missing data, so `n_unreachable_legs` is a lower bound.
    """
    leg = LegSanity(n_stops=len(our_stops))
    points = [(s.lat, s.lon) for s in our_stops]
    leg.max_leg_km_straight = max_leg_km(points)

    raw_leg = trace.get("max_leg_seconds")
    if isinstance(raw_leg, (int, float)) and not isinstance(raw_leg, bool):
        leg.max_leg_seconds = float(raw_leg)
    leg.unreachable_sentinel = _saturated(leg.max_leg_seconds) or _saturated(
        trace.get("walk_seconds")
    ) or _saturated(trace.get("total_seconds"))
    if leg.unreachable_sentinel:
        leg.n_unreachable_legs = 1

    length_km = summary.get("length_km")
    time_s = summary.get("time_seconds")
    if (
        isinstance(length_km, (int, float))
        and isinstance(time_s, (int, float))
        and length_km > 0
        and time_s > 0
    ):
        leg.avg_speed_kmh = float(length_km) / (float(time_s) / 3600.0)
        if leg.max_leg_seconds is not None:
            leg.max_leg_km = leg.max_leg_seconds / 3600.0 * leg.avg_speed_kmh

    for value in (leg.max_leg_km, leg.max_leg_km_straight):
        if value is not None and value > MAX_WALK_LEG_KM:
            leg.over_cap = True
            break

    leg.duplicate_pairs = find_duplicate_stop_pairs(our_stops)
    leg.n_duplicate_stops = len(leg.duplicate_pairs)
    leg.geometry_missing = "shape" in (raw or {}) and not raw.get("shape")
    return leg


def _saturated(value) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return not math.isfinite(float(value)) or float(value) >= UNREACHABLE_S


def build_request(
    query: str,
    budget_minutes: int | None,
    origin_lat: float | None = None,
    origin_lon: float | None = None,
) -> dict:
    """The exact body sent to POST /routes/generate (also what a row records)."""
    payload: dict = {
        "query": query,
        "time_budget_minutes": budget_minutes,
    }
    if origin_lat is not None and origin_lon is not None:
        payload["origin"] = {"lat": origin_lat, "lon": origin_lon}
    return payload


def post_generate(base_url: str, payload: dict) -> tuple[int, dict]:
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


def call_generate(
    base_url: str,
    query: str,
    budget_minutes: int | None,
    origin_lat: float | None = None,
    origin_lon: float | None = None,
) -> dict:
    """POST /routes/generate → parsed dict (raw JSON)."""
    _status, raw = post_generate(
        base_url, build_request(query, budget_minutes, origin_lat, origin_lon)
    )
    return raw


def route_origin(golden: GoldenRoute) -> tuple[float | None, float | None]:
    """Origin estimate: the centroid of the reference stops."""
    if not golden.stops:
        return None, None
    lat = sum(s.lat for s in golden.stops) / len(golden.stops)
    lon = sum(s.lon for s in golden.stops) / len(golden.stops)
    return lat, lon


def evaluate(golden: GoldenRoute, base_url: str) -> EvaluationResult:
    """One live run. Same scorer the replay path uses, via call_generate()."""
    lat, lon = route_origin(golden)
    t0 = time.monotonic()
    try:
        raw = call_generate(base_url, golden.query_ru, golden.budget_minutes, lat, lon)
    except urllib.error.HTTPError as exc:
        return score_response(
            golden, None, http_status=exc.code, api_error=_http_error_detail(exc),
            latency_s=time.monotonic() - t0,
        )
    except urllib.error.URLError as exc:
        return score_response(
            golden, None, api_error=str(exc), latency_s=time.monotonic() - t0
        )
    return score_response(
        golden, raw, latency_s=time.monotonic() - t0
    )


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace")
        return json.loads(body).get("detail") or body
    except Exception:
        return f"HTTP {exc.code}"


def score_response(
    golden: GoldenRoute,
    raw: dict | None,
    *,
    http_status: int | None = None,
    api_error: str | None = None,
    latency_s: float = 0.0,
) -> EvaluationResult:
    """Every metric for one run, from a response and nothing else.

    Shared by live, snapshot and replay; pure — no clock, no RNG, no I/O.
    """
    result = EvaluationResult(
        golden=golden, api_error=api_error, latency_s=latency_s, http_status=http_status
    )
    if api_error is not None or (http_status is not None and http_status >= 400):
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
    trace = ((raw or {}).get("debug") or {}).get("trace") or {}
    result.intent_source = ((raw or {}).get("debug") or {}).get("intent_source")
    result.stops_dropped = int(budget_info.get("stops_dropped") or 0)

    pool = probe_stage1_pool(raw or {})
    if pool:
        result.candidate_ids = pool["ids"]
        result.candidate_ids_source = "stage1_pool"
    else:
        result.candidate_ids = [s.place_id for s in result.our_stops if s.place_id is not None]
        result.candidate_ids_source = "route_points"

    _score_coverage(result)
    _score_reference(result)
    _score_budget(result, summary, budget_info)
    result.stage = stage_split(golden, result.our_stops, pool)
    result.leg = leg_sanity(result.our_stops, trace, summary, raw or {})
    result.failures.extend(_leg_failures(result.leg))
    return result


def _our_stops(raw: dict) -> list[OurStop]:
    out: list[OurStop] = []
    for p in raw.get("points") or []:
        out.append(OurStop(
            name=p.get("name", ""),
            lat=float(p.get("lat") or 0.0),
            lon=float(p.get("lon") or 0.0),
            visit_minutes=p.get("visit_minutes"),
            place_id=p.get("id") if isinstance(p.get("id"), int) else None,
        ))
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
    """Reference walk, tau over the shared stops, walk lengths and detour.

    Rebuilt per run — a pure function of the committed coordinates.
    """
    golden = result.golden
    ref = build_reference_walk(golden.stops)
    result.ref_walk_km = ref.distance_km

    ref_shared = [
        result.matches[gi][1] for gi in ref.order if result.matches[gi][1] is not None
    ]
    result.shared_stops = len(ref_shared)
    result.kendall_tau = kendall_tau(ref_shared, sorted(ref_shared))

    our_points = [(s.lat, s.lon) for s in result.our_stops]
    result.our_walk_km = (
        walk_distance_km(our_points, list(range(len(our_points))))
        if our_points
        else None
    )
    result.detour_km = detour_km(result.our_walk_km, ref.distance_km)


def _score_budget(result: EvaluationResult, summary: dict, budget_info: dict) -> None:
    walk_s = summary.get("time_seconds") or budget_info.get("walk_minutes", 0) * 60
    walk_min = walk_s / 60.0
    if result.golden.est_walk_minutes is not None:
        result.walk_diff_min = walk_min - result.golden.est_walk_minutes
    elif budget_info.get("walk_minutes"):
        result.walk_diff_min = walk_min - budget_info.get("walk_minutes")
    result.our_walk_km_net = summary.get("length_km")
    result.budget_fit = bool(budget_info.get("fits", False))


def _leg_failures(leg: LegSanity) -> list[Failure]:
    out: list[Failure] = []
    if leg.unreachable_sentinel:
        out.append(Failure(
            kind="unreachable_leg",
            detail=(
                f"Valhalla sentinel {UNREACHABLE_S:.0f} in the trace: >= 1 hop is "
                "not connectable (the matrix is not shipped, so the count is a "
                "lower bound)"
            ),
        ))
    if leg.geometry_missing:
        out.append(Failure(
            kind="geometry_missing", detail="response carried an empty shape"
        ))
    if leg.n_duplicate_stops:
        names = "; ".join(
            f"{p['names'][0]} ~ {p['names'][1]} ({p['dist_m']:.0f} m, {p['rule']})"
            for p in leg.duplicate_pairs
        )
        out.append(Failure(
            kind="duplicate_stop", detail=f"{leg.n_duplicate_stops} duplicate pair(s): {names}"
        ))
    if leg.over_cap:
        out.append(Failure(
            kind="leg_over_cap",
            detail=f"a leg exceeds MAX_WALK_LEG_KM={MAX_WALK_LEG_KM}",
        ))
    return out


def percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile of an already sorted list (clamped)."""
    if not sorted_values:
        return float("nan")
    k = min(len(sorted_values) - 1, max(0, int(q * len(sorted_values))))
    return sorted_values[k]


def resample_indices(n: int, samples: int, seed: int) -> list[tuple[int, ...]]:
    """`samples` bootstrap resamples of `n` case indices, from a seeded RNG.

    Cached per (n, samples, seed) so every metric in one report shares the SAME indices.
    """
    key = (n, samples, seed)
    cached = _RESAMPLE_CACHE.get(key)
    if cached is None:
        rng = random.Random(seed)
        cached = [
            tuple(rng.randrange(n) for _ in range(n))
            for _ in range(samples)
        ]
        _RESAMPLE_CACHE[key] = cached
    return cached


_RESAMPLE_CACHE: dict[tuple[int, int, int], list[tuple[int, ...]]] = {}


def bootstrap_ci(
    values: list[float],
    indices: list[tuple[int, ...]],
    alpha: float = BOOTSTRAP_ALPHA,
) -> dict:
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
) -> dict:
    """Paired bootstrap on the difference A − B over the shared cases.

    A single paired case gets a difference but no interval and no p-value.
    """
    n = len(a_values)
    if n != len(b_values) or n == 0:
        return {"diff": None, "lo": None, "hi": None, "p": None, "n": n}
    diff = sum(a_values) / n - sum(b_values) / n
    if n == 1:
        return {"diff": diff, "lo": None, "hi": None, "p": None, "n": 1}
    diffs = [
        sum(a_values[i] for i in idx) / n - sum(b_values[i] for i in idx) / n
        for idx in indices
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
        cached = _sha256_text(golden.path.read_text(encoding="utf-8"))
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
) -> dict:
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
) -> dict:
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


def write_meta(meta: dict, snapshot_dir: Path, append: bool) -> Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / "rows.jsonl"
    if not append:
        path.write_text("", encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
    return path


def append_row(row: dict, path: Path) -> None:
    """Append one row. A crashed run therefore leaves a replayable partial file."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_snapshot(snapshot_dir: Path) -> tuple[dict, list[dict]]:
    """Read a snapshot dir → (meta, rows).

    Only `rows*.jsonl` is snapshot data; unknown record kinds are dropped.
    """
    files = sorted(snapshot_dir.glob("rows*.jsonl"))
    if not files:
        raise FileNotFoundError(
            f"no rows*.jsonl snapshot file in {snapshot_dir} "
            "(a snapshot dir holds rows.jsonl plus the reports)"
        )
    meta: dict = {}
    rows: list[dict] = []
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


def _r(value, digits: int = 6):
    """Round a float for the JSONL so a diff of two snapshots is readable."""
    if isinstance(value, float) and not isinstance(value, bool):
        return round(value, digits)
    if isinstance(value, dict):
        return {k: _r(v, digits) for k, v in value.items()}
    if isinstance(value, list):
        return [_r(v, digits) for v in value]
    return value


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
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def _attr(runs: list[EvaluationResult], path: str) -> list[float]:
    """Defined values of `path` across runs.

    Gated hard failures and undefined (None) values are skipped, not averaged in.
    """
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
    ("leg.max_leg_km", "max leg km (from trace)", 2),
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
    ("leg.max_leg_km", "max leg km"),
)


def _results(groups: list[list[RunRecord]]) -> list[EvaluationResult]:
    return [rec.result for g in groups for rec in g]


def per_case_scores(groups: list[list[RunRecord]]) -> dict[str, dict[str, float | None]]:
    """One score per case per metric: the mean over that case's defined runs.

    The case is the resampling unit for every CI and paired bootstrap.
    """
    out: dict[str, dict[str, float | None]] = {}
    for g in groups:
        if not g:
            continue
        results = [r.result for r in g]
        out[g[0].case] = {
            path: case_value(results, path) for path, _label, _d in CI_METRICS
        }
    return out


def overall_stats(
    per_case: dict[str, dict[str, float | None]],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = BOOTSTRAP_ALPHA,
) -> dict[str, dict]:
    """Bootstrap over CASES for every metric in CI_METRICS."""
    out: dict[str, dict] = {}
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


def noise_floor(groups: list[list[RunRecord]]) -> dict[str, dict]:
    """Mean within-case spread across repeats — the LLM-sampling noise floor.

    Reported next to every delta; a smaller change is indistinguishable from a re-run.
    """
    out: dict[str, dict] = {}
    for path, label, _d in CI_METRICS:
        spreads, stds, n_cases = [], [], 0
        for g in groups:
            vals = _attr([r.result for r in g], path)
            if len(vals) < 2:
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


def failure_summary(groups: list[list[RunRecord]]) -> dict:
    """Hard failures and leg-sanity defects — counted, never averaged in."""
    by_kind: dict[str, int] = {}
    gated: list[dict] = []
    defects: list[dict] = []
    details: list[dict] = []
    total_runs = 0
    for g in groups:
        for rec in g:
            total_runs += 1
            for f in rec.result.failures:
                by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
                row = {"case": rec.case, "repeat": rec.repeat, **f.as_dict()}
                (gated if f.gated else defects).append(row)
                details.append(row)
    return {
        "total_runs": total_runs,
        "n_failed_runs": len(gated),
        "gated_runs": len(gated),
        "by_kind": by_kind,
        "leg_sanity_defects": len(defects),
        "gated": gated,
        "details": details,
    }


def _f(v: float | None, decimals: int = 3) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}"


def _pad(s: str, w: int) -> str:
    return s.ljust(w)


def _ci_cell(stats: dict, decimals: int = 3) -> str:
    if stats.get("mean") is None:
        return "n/a"
    if stats.get("n", 0) < 2:
        return f"{stats['mean']:.{decimals}f} (n=1, no CI)"
    return (
        f"{stats['mean']:.{decimals}f} "
        f"[{stats['lo']:.{decimals}f}, {stats['hi']:.{decimals}f}]"
    )


def _stage2_is_trivial(overall: dict[str, dict]) -> bool:
    """True when stage-2 recall is 1.0 for want of a pool, not for want of skill.

    Under the route-points fallback the pool is the route, so the conditional is vacuous.
    """
    stats = overall.get("stage.route_recall_given_pool", {})
    return stats.get("mean") is not None and stats["mean"] >= 1.0


COLS = [
    "name", "s1", "s2", "recall", "prec", "tau", "detour", "walk_diff", "fit",
    "dup", "unr", "lat", "hf",
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
    "name": 30, "s1": 11, "s2": 11, "recall": 12, "prec": 11, "tau": 11,
    "detour": 13, "walk_diff": 12, "fit": 5, "dup": 5, "unr": 5, "lat": 11, "hf": 4,
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
        row = " │ ".join(
            cells[k].rjust(COL_WIDTH[k]) if k != "name" else name for k in COLS
        )
        errs = [r.api_error for r in results if r.api_error]
        print(f"│ {row} │")
        for err in errs:
            print(f"│ {'':{COL_WIDTH['name']}} │{'':{sum(COL_WIDTH[k] for k in COLS[1:]) + 3 * (len(COLS) - 2)}}│ {err[:70]} │")

    print(f"{'':─^{total_width}}")
    print()
    print(f"  S1 pool = stage-1 retrieval proxy (any returned stop within "
          f"{STAGE1_PROXY_RADIUS_M:.0f} m of a reference stop)")
    print("  S2 | S1 = stage-2 recall conditional on a stage-1 hit")
    print("  Dup = duplicate-POI pairs, Unr = unreachable hops (UNREACHABLE_S),")
    print(f"  HF = gated hard failures. mean±spread over {repeat} repeat(s).")
    print()


def print_ci_table(
    overall: dict[str, dict],
    per_case: dict[str, dict],
    noise: dict[str, dict],
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
        print(f"{stats.get('label', path):<38} {cell:<34} {stats.get('n', 0):>3}"
              f"  {('n/a' if nf is None else f'{nf:.{dec}f}')}")
    print()
    print(f"  noise floor = mean within-case spread across repeats; reference "
          f"measurement: {REFERENCE_NOISE_FLOOR}")
    print(f"  {len(per_case)} case(s) — with n that small the CI is the finding.")
    if _stage2_is_trivial(overall):
        print("  ⚠ stage-2 recall reads 1.000 BY CONSTRUCTION while the pool "
              "fallback is the")
        print("    route's own stops: the route cannot miss a stop stage 1 found. "
              "It becomes")
        print("    a measurement only when the API exposes a candidate pool "
              "larger than the route.")
    print()


def print_failures(summary: dict, overall: dict | None = None) -> None:
    """Hard failures + leg-sanity defects. Deliberately outside every score."""
    print("─" * 78)
    print(f"HARD FAILURES (not part of any score above) — {summary['n_failed_runs']}"
          f" of {summary['total_runs']} run(s) gated out of the means")
    if not summary["by_kind"]:
        print("  none")
    for kind, count in sorted(summary["by_kind"].items()):
        gated = "gated" if kind in GATED_FAILURE_KINDS else "counted"
        print(f"  {kind:<20} {count:>3}   ({gated})")
    for row in summary["gated"]:
        print(f"    ✗ {row['case']}#{row['repeat']}: {row['detail']}")
    defects = [
        d for d in summary["details"] if not d["gated"]
    ]
    print(f"  leg-sanity defects kept in the means: {len(defects)} "
          "(hiding them by dropping the run would defeat the check)")
    for row in defects:
        print(f"    ! {row['case']}#{row['repeat']}: {row['detail']}")
    if overall:
        rec = overall.get("recall_at_k", {})
        print(f"  headline (for reference only): Rec@K {_ci_cell(rec)}")
    print()


def _agg(runs: list[EvaluationResult], path: str) -> dict:
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
    *,
    mode: str,
    generated_at: str,
    snapshot: dict,
    per_case: dict,
    overall: dict,
    noise: dict,
    failures: dict,
    drift_rows: list[dict],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.json"
    results = []
    for runs in groups:
        eval_runs = [r.result for r in runs]
        r0 = eval_runs[0]
        errs = [r.api_error for r in eval_runs if r.api_error]
        results.append({
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
                        if rec.result.kendall_tau is not None else None
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
                        {"id": s.place_id, "name": s.name, "lat": s.lat, "lon": s.lon,
                         "visit_minutes": s.visit_minutes}
                        for s in rec.result.our_stops
                    ],
                }
                for rec in runs
            ],
        })

    report = {
        "schema": REPORT_SCHEMA,
        "mode": mode,
        "generated_at": generated_at,
        "n_routes": len(groups),
        "repeat": len(groups[0]) if groups else 0,
        "n_runs": sum(len(g) for g in groups),
        "snapshot": snapshot,
        "bootstrap": {
            "samples": samples,
            "seed": seed,
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
                f"grade weights {GRADE_WEIGHTS}; an ungraded stop counts as "
                f"'{DEFAULT_GRADE}'."
            ),
            "max_leg_km": (
                "trace.max_leg_seconds converted at the route's own average speed; "
                f"a value over MAX_WALK_LEG_KM={MAX_WALK_LEG_KM} is a leg-sanity "
                "defect. The straight-line maximum is kept as an offline lower bound."
            ),
            "n_unreachable_legs": (
                "lower bound: the UNREACHABLE_S sentinel saturates the trace but "
                "the matrix itself is not shipped, so the exact hop count is not "
                "recoverable offline."
            ),
            "duplicates": (
                f"same POI twice inside one route: <{DUPLICATE_RADIUS_M:.0f} m "
                f"(any name), identical normalised name <{DUPLICATE_NAME_RADIUS_M:.0f} m, "
                f"or name token overlap >= {DUPLICATE_SIM} <{DUPLICATE_SIM_RADIUS_M:.0f} m"
            ),
            "hard_failures": (
                f"gated kinds {sorted(GATED_FAILURE_KINDS)} are dropped from every "
                "quality mean and reported outside every score; leg-sanity defects "
                "(duplicate_stop, leg_over_cap) stay in the means and are counted."
            ),
        },
        "per_case_scores": per_case,
        "overall": _r(overall, 4),
        "noise_floor": {
            "computed": _r(noise, 4),
            "reference_measurement": REFERENCE_NOISE_FLOOR,
        },
        "hard_failures": failures,
        "metrics_drift_vs_snapshot": drift_rows,
        "results": results,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  JSON report → {path}")
    return path


def write_metrics_jsonl(
    groups: list[list[RunRecord]],
    out_dir: Path,
    overall: dict,
    failures: dict,
) -> Path:
    """One line per case — the shape a CI job diffs between runs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.metrics.jsonl"
    lines = []
    for runs in groups:
        eval_runs = [r.result for r in runs]
        case = runs[0].case
        lines.append(json.dumps({
            "case": case,
            "name": eval_runs[0].golden.name,
            "n_runs": len(runs),
            "scores": {
                path_: _r(case_value(eval_runs, path_), 4) for path_, _l, _d in CI_METRICS
            },
            "hard_failures": [f.as_dict() for f in eval_runs[0].failures],
            "n_duplicate_stops": sum(r.leg.n_duplicate_stops for r in eval_runs),
            "n_unreachable_legs": sum(r.leg.n_unreachable_legs for r in eval_runs),
        }, ensure_ascii=False, sort_keys=True))
    lines.append(json.dumps({
        "case": "__overall__",
        "mean_and_ci95": {
            path_: _r(overall.get(path_, {}), 4) for path_, _l, _d in CI_METRICS
        },
        "n_hard_failure_runs": failures["n_failed_runs"],
        "n_leg_sanity_defects": failures["leg_sanity_defects"],
    }, ensure_ascii=False, sort_keys=True))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  metrics JSONL → {path}")
    return path


def write_md_report(
    groups: list[list[RunRecord]],
    out_dir: Path,
    *,
    mode: str,
    generated_at: str,
    snapshot: dict,
    overall: dict,
    noise: dict,
    failures: dict,
    drift_rows: list[dict],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.md"
    repeat = len(groups[0]) if groups else 0
    L: list[str] = [
        "# Benchmark Report: Grodno Route Planner",
        "",
        f"**Mode:** `{mode}` · **Generated:** {generated_at}",
        "",
        f"**Runs per case:** {repeat} · **cases:** {len(groups)} · "
        f"**runs:** {sum(len(g) for g in groups)}",
        "",
    ]
    if snapshot:
        L += [
            f"**Snapshot:** `{snapshot.get('dir', '—')}` · "
            f"{snapshot.get('n_rows', 0)} row(s) · "
            f"git `{snapshot.get('git_sha', '—')}`",
            "",
        ]
    if drift_rows:
        L += [
            f"> ⚠ {len(drift_rows)} row(s) recomputed to metrics that differ from "
            "the values recorded in the snapshot — the metric code changed since "
            "the run.",
            "",
        ]

    L += [
        "## Per-case scores (`mean±spread` over repeats)",
        "",
        "| case | S1 pool | S2 \\| S1 | Rec@K | prec | τ | detour km | Δwalk | fit "
        "| dup | unr | HF |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for runs in groups:
        eval_runs = [r.result for r in runs]
        L.append(
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

    L += [
        "",
        f"## Overall — bootstrap over cases (B={samples}, seed={seed})",
        "",
        "| metric | mean [95 % CI] | n cases | noise floor (within-case spread) |",
        "|---|---|---|---|",
    ]
    for path_, _label, dec in CI_METRICS:
        stats = overall.get(path_, {})
        nf = noise.get(path_, {}).get("mean_within_case_spread")
        L.append(
            f"| {stats.get('label', path_)} | {_ci_cell(stats, dec)} | "
            f"{stats.get('n', 0)} | "
            f"{'n/a' if nf is None else f'{nf:.{dec}f}'} |"
        )
    L += [
        "",
        f"Reference noise floor: {REFERENCE_NOISE_FLOOR}.",
        "",
        "## Hard failures — outside every score above",
        "",
        f"**{failures['n_failed_runs']} of {failures['total_runs']} run(s) gated "
        f"out of the quality means.** Counts by kind:",
        "",
    ]
    if not failures["by_kind"]:
        L.append("- none")
    for kind, count in sorted(failures["by_kind"].items()):
        tag = "gated" if kind in GATED_FAILURE_KINDS else "counted, kept in means"
        L.append(f"- `{kind}`: {count} ({tag})")
    if failures["gated"]:
        L += ["", "| case | repeat | kind | detail |", "|---|---|---|---|"]
        for row in failures["gated"]:
            L.append(
                f"| {row['case']} | {row['repeat']} | {row['kind']} | {row['detail']} |"
            )
    L += [
        "",
        f"Leg-sanity defects kept in the means: {failures['leg_sanity_defects']}.",
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
        "Grades: `" + ", ".join(f"{k}={v}" for k, v in GRADE_WEIGHTS.items()) +
        f"`, ungraded treated as `{DEFAULT_GRADE}`.",
        "",
        "## Determinism",
        "",
        "`--replay` recomputes every metric from the recorded rows with no agent, no",
        "Valhalla and no clock: two replays of the same snapshot are byte-identical,",
        "which is what makes a metric change reviewable as a diff rather than a claim.",
        "",
    ]
    path.write_text("\n".join(L), encoding="utf-8")
    print(f"  Markdown report → {path}")
    return path


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
        routes.append(GoldenRoute(
            name=data["name"],
            source=data.get("source", ""),
            query_ru=data["query_ru"],
            budget_minutes=data.get("budget_minutes", 120),
            stops=stops,
            est_walk_minutes=data.get("est_walk_minutes"),
            path=p,
            case=p.stem,
        ))
    return routes


def load_golden_map() -> dict[str, GoldenRoute]:
    return {r.case: r for r in load_golden_routes(BENCH_ROUTES)}


GOLDEN_REQUIRED_TOP = frozenset({"id", "locale", "query", "filters", "expectations"})
GOLDEN_OPTIONAL_TOP = frozenset({"parity_group"})
GOLDEN_FILTER_KEYS = frozenset({
    "party_children", "hard_services", "interests", "avoid",
    "time_budget_minutes", "origin", "result_mode",
})
GOLDEN_REQUIRED_EXPECTATIONS = frozenset({
    "must_contain_categories", "must_not_contain_categories", "expected_status",
    "max_total_minutes", "in_region",
})
GOLDEN_OPTIONAL_EXPECTATIONS = frozenset({
    "must_contain_names", "expected_result_mode", "allow_empty", "min_places",
    "status_note",
})
GOLDEN_EXPECTATION_KEYS = GOLDEN_REQUIRED_EXPECTATIONS | GOLDEN_OPTIONAL_EXPECTATIONS

LOCALES = ("ru", "en")
RESULT_MODES = ("route", "catalogue")
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
ORIGIN_BOUNDS = {"lat": (44.0, 62.0), "lon": (19.0, 42.0)}


class GoldenCaseError(ValueError):
    """A golden case that does not satisfy the documented schema."""


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
    errs: list[str] = []
    if not isinstance(data, dict):
        return [f"{where}: top level must be an object"]

    unknown_top = set(data) - GOLDEN_REQUIRED_TOP - GOLDEN_OPTIONAL_TOP
    for key in sorted(unknown_top):
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

    query = data.get("query")
    if not isinstance(query, str) or not (3 <= len(query.strip()) <= 500):
        errs.append(f"{where}: query must be a string of 3..500 characters")
    elif locale in LOCALES:
        has_cyrillic = bool(_CYRILLIC_RE.search(query))
        if locale == "en" and has_cyrillic:
            errs.append(f"{where}: locale is 'en' but the query contains Cyrillic")
        if locale == "ru" and not has_cyrillic:
            errs.append(f"{where}: locale is 'ru' but the query contains no Cyrillic")

    group = data.get("parity_group")
    if group is not None and (not isinstance(group, str) or not group.strip()):
        errs.append(f"{where}: parity_group must be a non-empty string when present")

    filters = data.get("filters")
    if not isinstance(filters, dict):
        errs.append(f"{where}: filters must be an object")
    else:
        for key in sorted(set(filters) - GOLDEN_FILTER_KEYS):
            errs.append(f"{where}: unknown filters key {key!r}")
        for key in sorted(GOLDEN_FILTER_KEYS - set(filters)):
            errs.append(f"{where}: missing filters key {key!r}")
        children = filters.get("party_children")
        if children is not None and not (
            isinstance(children, int) and not isinstance(children, bool)
            and 0 <= children <= 20
        ):
            errs.append(f"{where}: party_children must be null or an int in 0..20")
        for key in ("hard_services", "interests", "avoid"):
            if key in filters:
                _validate_code_list(f"{where}: filters.{key}", filters[key], errs)
        hard = {_norm_code(c) for c in filters.get("hard_services") or []}
        avoid = {_norm_code(c) for c in filters.get("avoid") or []}
        interests = {_norm_code(c) for c in filters.get("interests") or []}
        both = hard & avoid
        if both:
            errs.append(
                f"{where}: filters.hard_services and filters.avoid overlap "
                f"({sorted(both)}) — a code cannot be mandatory and forbidden"
            )
        if interests & avoid:
            errs.append(
                f"{where}: filters.interests and filters.avoid overlap "
                f"({sorted(interests & avoid)})"
            )
        budget = filters.get("time_budget_minutes")
        if budget is not None and not (
            isinstance(budget, int) and not isinstance(budget, bool) and budget >= 0
        ):
            errs.append(f"{where}: filters.time_budget_minutes must be null or an int >= 0")
        elif budget is not None and budget > int(agent_constants.MAX_BUDGET_MIN):
            errs.append(
                f"{where}: filters.time_budget_minutes {budget} exceeds the API "
                f"maximum {agent_constants.MAX_BUDGET_MIN}"
            )
        origin = filters.get("origin")
        if origin is not None:
            if not isinstance(origin, dict) or set(origin) != {"lat", "lon"}:
                errs.append(f"{where}: filters.origin must be null or {{lat, lon}}")
            else:
                for axis, (lo, hi) in ORIGIN_BOUNDS.items():
                    value = origin.get(axis)
                    if not _is_num(value) or not (lo <= value <= hi):
                        errs.append(
                            f"{where}: filters.origin.{axis} must be a number in "
                            f"{lo}..{hi}, got {value!r}"
                        )
        if filters.get("result_mode") not in RESULT_MODES:
            errs.append(
                f"{where}: filters.result_mode must be one of {list(RESULT_MODES)}, "
                f"got {filters.get('result_mode')!r}"
            )

    exp = data.get("expectations")
    if not isinstance(exp, dict):
        errs.append(f"{where}: expectations must be an object")
    else:
        for key in sorted(set(exp) - GOLDEN_EXPECTATION_KEYS):
            errs.append(f"{where}: unknown expectations key {key!r}")
        for key in sorted(GOLDEN_REQUIRED_EXPECTATIONS - set(exp)):
            errs.append(f"{where}: missing expectations key {key!r}")
        for key in ("must_contain_categories", "must_not_contain_categories"):
            if key in exp:
                _validate_code_list(f"{where}: expectations.{key}", exp[key], errs)
        names = exp.get("must_contain_names")
        if names is not None and (
            not isinstance(names, list)
            or not names
            or any(not isinstance(n, str) or not n.strip() for n in names)
        ):
            errs.append(
                f"{where}: expectations.must_contain_names must be a non-empty "
                "list of non-empty strings when present"
            )
        statuses = exp.get("expected_status")
        if not isinstance(statuses, list) or not statuses:
            errs.append(f"{where}: expectations.expected_status must be a non-empty list")
        else:
            for status in statuses:
                if status not in STATUSES:
                    errs.append(
                        f"{where}: expectations.expected_status {status!r} is not one "
                        f"of {sorted(STATUSES)}"
                    )
        mode = exp.get("expected_result_mode")
        if mode is not None and mode not in RESULT_MODES:
            errs.append(f"{where}: expectations.expected_result_mode must be {RESULT_MODES}")
        cap = exp.get("max_total_minutes")
        if cap is not None and not (
            isinstance(cap, int) and not isinstance(cap, bool) and cap > 0
        ):
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
        if not isinstance(exp.get("in_region"), bool):
            errs.append(f"{where}: expectations.in_region must be a boolean")
        if "allow_empty" in exp and not isinstance(exp["allow_empty"], bool):
            errs.append(f"{where}: expectations.allow_empty must be a boolean")
        min_places = exp.get("min_places")
        if min_places is not None and not (
            isinstance(min_places, int) and not isinstance(min_places, bool) and min_places >= 0
        ):
            errs.append(f"{where}: expectations.min_places must be null or an int >= 0")
        elif min_places is not None and not exp.get("allow_empty") and min_places == 0:
            errs.append(
                f"{where}: expectations.min_places 0 with allow_empty false — an "
                "empty plan would pass; say allow_empty: true instead"
            )
        if exp.get("status_note") is not None and not isinstance(exp["status_note"], str):
            errs.append(f"{where}: expectations.status_note must be a string")
    return errs


def golden_case_from_dict(data: dict, path: Path | None = None) -> GoldenCase:
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
    """What must be identical across a parity group: filters and conditions.

    `status_note` is per-locale documentation; everything else must match.
    """
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


def _status_from_requirements(requirements: list) -> str:
    """Mirror of planner/verify.py::overall_status over a public requirements list.

    Restated so the benchmark can grade without importing the planner.
    """
    hard = [
        r for r in requirements
        if isinstance(r, dict) and r.get("strength") == "hard"
    ]
    states = {str(r.get("status")) for r in hard}
    if "unmet" in states:
        return "infeasible"
    if "uncertain" in states:
        return "degraded"
    if "pending" in states:
        return "pending"
    return "ready"


def derive_status(
    raw: dict | None,
    *,
    http_status: int | None = None,
    api_error: str | None = None,
) -> tuple[str, str]:
    """(status, source) for one response, using the best evidence available.

    `source` records which rung was used; a derived status is never passed off as the API's.
    """
    if api_error or (http_status is not None and http_status >= 400):
        code = int(http_status or 0)
        if 400 <= code < 500:
            return "rejected", "http_status"
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
    tokens = {t.replace("ё", "е") for t in _norm_name(name).split()}
    return {code for code in CANONICAL_CODES_NORM if code in tokens}


def _canon_codes(codes) -> list[str]:
    """Canonical spelling of normalised codes, for anything a human reads."""
    return [CANONICAL_CATEGORIES.get(c, c) for c in codes]


def stop_category_codes(stop: dict) -> set[str]:
    """The canonical codes one returned stop counts as.

    The point's own `category` is authoritative; the name only votes when it is missing.
    """
    category = _norm_code(stop.get("category"))
    if category:
        return {category}
    return codes_in_name(stop.get("name", ""))


def _ok(detail: str = "", **extra) -> dict:
    return {"ok": True, "detail": detail, **extra}


def _fail(detail: str, **extra) -> dict:
    return {"ok": False, "detail": detail, **extra}


def _unverified(detail: str, **extra) -> dict:
    return {"ok": True, "unverified": True, "detail": detail, **extra}


def response_total_minutes(raw: dict | None, points: list[dict]) -> float | None:
    """Total route minutes: `budget.total_minutes`, or walk + visits as a fallback.

    The fallback keeps an over-budget route from hiding behind a missing field.
    """
    raw = raw or {}
    budget = raw.get("budget") or {}
    total = budget.get("total_minutes")
    if _is_num(total):
        return float(total)
    summary = raw.get("summary") or {}
    walk_s = summary.get("time_seconds")
    visits = sum(
        p.get("visit_minutes") or 0 for p in points if _is_num(p.get("visit_minutes"))
    )
    if _is_num(walk_s):
        return float(walk_s) / 60.0 + float(visits)
    return None


def evaluate_compliance(
    case: GoldenCase,
    raw: dict | None,
    *,
    http_status: int | None = None,
    api_error: str | None = None,
) -> ComplianceVerdict:
    """Every expectation of one golden case against one response.

    Pure; `reason` is the first failed check in CHECK_PRIORITY, or "ok".
    """
    exp = case.expectations
    raw = raw or {}
    points = [p for p in (raw.get("points") or []) if isinstance(p, dict)]
    status, source = derive_status(raw, http_status=http_status, api_error=api_error)
    checks: dict[str, dict] = {}

    expected_status = [str(s).strip().lower() for s in exp["expected_status"]]
    checks[CHECK_WRONG_STATUS] = (
        _ok(f"{status} ({source}) is in {expected_status}")
        if status in expected_status
        else _fail(f"status {status} ({source}) is not one of {expected_status}")
    )
    if status == "error":
        checks[CHECK_API_ERROR] = _fail(
            api_error or f"the backend failed with HTTP {http_status} — no plan"
        )
    elif api_error or (http_status is not None and http_status >= 400):
        checks[CHECK_API_ERROR] = _ok(
            f"the backend refused the request (HTTP {http_status}) — an honest "
            f"refusal, judged by the status check"
        )
    else:
        checks[CHECK_API_ERROR] = _ok("the backend answered with a plan")

    allow_empty = bool(exp.get("allow_empty"))
    min_places = exp.get("min_places")
    if min_places is None:
        min_places = 0 if allow_empty else 1
    if len(points) < min_places:
        detail = f"{len(points)} stop(s) returned, at least {min_places} required"
        if not points and allow_empty:
            detail = "empty plan, which this case allows"
        checks[CHECK_TOO_FEW_PLACES] = (
            _ok(detail) if len(points) >= min_places else _fail(detail)
        )
    else:
        checks[CHECK_TOO_FEW_PLACES] = _ok(
            f"{len(points)} stop(s) returned (>= {min_places})"
        )

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
    wanted = [_norm_code(c) for c in exp["must_contain_categories"]]
    missing = [c for c in wanted if c not in seen]
    category_evidence = {
        "missing": _canon_codes(missing),
        "seen": sorted(_canon_codes(seen)),
    }
    checks[CHECK_MISSING_MANDATORY_CATEGORY] = (
        _ok(f"every mandatory category is present {_canon_codes(wanted)}", **category_evidence)
        if not missing
        else _fail(
            f"missing mandatory category(ies) {category_evidence['missing']}"
            f"; the plan has {category_evidence['seen'] or 'no categories'}",
            **category_evidence,
        )
    )
    forbidden = [_norm_code(c) for c in exp["must_not_contain_categories"]]
    present = [c for c in forbidden if c in stop_codes]
    checks[CHECK_FORBIDDEN_CATEGORY_PRESENT] = (
        _ok(f"no forbidden category present {_canon_codes(forbidden)}")
        if not present
        else _fail(
            f"forbidden category(ies) present "
            f"{[CANONICAL_CATEGORIES.get(c, c) for c in present]}"
        )
    )

    names = exp.get("must_contain_names") or []
    if names:
        missing_names = [
            n for n in names
            if not any(_norm_code(n) in _norm_name(p.get("name", "")) for p in points)
        ]
        checks[CHECK_MISSING_NAMED_PLACE] = (
            _ok(f"every named place is on the plan {names}")
            if not missing_names
            else _fail(
                f"missing named place(s) {missing_names}; plan names "
                f"{[p.get('name') for p in points][:6]}"
            )
        )

    if exp.get("in_region"):
        offenders: list[str] = []
        for stop in points:
            lat, lon = stop.get("lat"), stop.get("lon")
            if not (_is_num(lat) and _is_num(lon)):
                offenders.append(f"{stop.get('name')!r} has no coordinates")
            elif not inside_project_area(float(lat), float(lon)):
                offenders.append(
                    f"{stop.get('name')!r} at {lat:.4f},{lon:.4f} is outside "
                    "Grodno ADM1"
                )
        checks[CHECK_OUT_OF_REGION_POINT] = (
            _ok(f"all {len(points)} point(s) inside Grodno ADM1")
            if not offenders
            else _fail("; ".join(offenders[:4]))
        )

    cap = exp.get("max_total_minutes")
    if cap is None:
        checks[CHECK_OVER_BUDGET] = _ok("no time cap in this case")
    else:
        total = response_total_minutes(raw, points)
        if total is None:
            checks[CHECK_OVER_BUDGET] = _unverified(
                f"cap {cap} min stated but the response reports no total"
            )
        elif total > cap + 1e-9:
            checks[CHECK_OVER_BUDGET] = _fail(
                f"total {total:.1f} min exceeds the {cap} min cap"
            )
        else:
            checks[CHECK_OVER_BUDGET] = _ok(f"total {total:.1f} min <= {cap} min cap")

    expected_mode = exp.get("expected_result_mode")
    if expected_mode is None:
        checks[CHECK_RESULT_MODE] = _ok("no result mode expected")
    else:
        actual = _norm_code(raw.get("result_mode") or raw.get("mode"))
        if actual:
            checks[CHECK_RESULT_MODE] = (
                _ok(f"result_mode {actual}")
                if actual == _norm_code(expected_mode)
                else _fail(f"result_mode {actual} != expected {expected_mode}")
            )
        else:
            checks[CHECK_RESULT_MODE] = _unverified(
                f"expected result_mode {expected_mode}, but the response carries no "
                "result_mode marker (the API does not expose one yet)"
            )

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


def parity_verdict(
    group: str,
    members: list[GoldenCase],
    verdicts: dict[str, ComplianceVerdict],
) -> ComplianceVerdict:
    """Did the same request in RU and EN get the same KIND of answer?

    Status, failed checks and mandatory categories are compared, not the stop lists.
    """
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
) -> dict:
    """Per-case PASS/FAIL plus the aggregate rate over cases AND parity groups.

    The rate is over units: one per golden case, one more per parity group.
    """
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
    unverified = sorted({
        f"{v.case_id}:{code}"
        for v in case_verdicts
        for code in v.unverified_checks
    })
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


def build_golden_request(case: GoldenCase) -> dict:
    """The exact body one golden case sends: the query plus its explicit filters.

    The filters go on the wire as GenerateReq fields, not prose.
    """
    filters = case.filters
    payload: dict = {
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
    return payload


@dataclass
class GoldenRun:
    """One (case, repeat) of a golden run — what the report and the snapshot store."""

    case: GoldenCase
    repeat: int
    request: dict
    http_status: int | None = None
    api_error: str | None = None
    response: dict | None = None
    latency_s: float = 0.0
    verdict: ComplianceVerdict | None = None
    ts: str | None = None
    git_sha: str | None = None


def summarise_repeats(case: GoldenCase, runs: list[GoldenRun]) -> ComplianceVerdict:
    """One verdict per case over its repeats: PASS only when every repeat passed.

    A condition that survives one run out of N is a flake, so the case fails.
    """
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
    """Call the agent once per (case, repeat) and score each response.

    Writes nothing: `write_golden_snapshot` owns the snapshot and runs once, after.
    """
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
            raw: dict | None = None
            err: str | None = None
            try:
                status, raw = post_generate(base_url, request)
            except urllib.error.HTTPError as exc:
                status = exc.code
                err = _http_error_detail(exc)
            except urllib.error.URLError as exc:
                err = str(exc)
            latency = time.monotonic() - t0
            verdict = evaluate_compliance(
                case, raw, http_status=status, api_error=err
            )
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


def make_golden_row(run: GoldenRun, base_url: str) -> dict:
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
) -> dict:
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
    meta: dict | None = None,
) -> Path:
    """Write the golden snapshot (one meta record + one row per run).

    Meta first, then the rows that ``rescore_golden_rows`` replays offline.
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / GOLDEN_SNAPSHOT_FILE
    cases = [run.case for run in runs]
    header = meta or golden_snapshot_meta(
        cases, base_url, max((r.repeat for r in runs), default=1)
    )
    with path.open("a" if append else "w", encoding="utf-8") as fh:
        fh.write(json.dumps(header, ensure_ascii=False) + "\n")
        for run in runs:
            fh.write(json.dumps(make_golden_row(run, base_url), ensure_ascii=False) + "\n")
    return path


def load_golden_snapshot(snapshot_dir: Path) -> tuple[dict, list[dict]]:
    """Read golden_rows.jsonl → (meta, rows). Never touches the network."""
    path = Path(snapshot_dir) / GOLDEN_SNAPSHOT_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"no {GOLDEN_SNAPSHOT_FILE} in {snapshot_dir} "
            f"(a golden snapshot holds that file plus the reports)"
        )
    meta: dict = {}
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
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


def rescore_golden_rows(
    rows: list[dict], cases: list[GoldenCase], base_url: str
) -> list[GoldenRun]:
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
        out.append(GoldenRun(
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
        ))
    return out


def golden_verdicts(runs: list[GoldenRun], cases: list[GoldenCase]) -> tuple[
    dict[str, ComplianceVerdict], list[ComplianceVerdict]
]:
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
    summary: dict,
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
    *,
    mode: str,
    generated_at: str,
    base_url: str,
    snapshot: dict,
    cases: list[GoldenCase],
    runs: list[GoldenRun],
    summary: dict,
) -> None:
    """compliance.json + compliance.md + compliance.metrics.jsonl (one line/case)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    per_case: dict[str, list[GoldenRun]] = {}
    for run in runs:
        per_case.setdefault(run.case.id, []).append(run)

    payload = {
        "schema": REPORT_SCHEMA,
        "mode": mode,
        "generated_at": generated_at,
        "base_url": base_url,
        "snapshot": snapshot,
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
                                [p for p in ((run.response or {}).get("points") or [])
                                 if isinstance(p, dict)],
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
    lines.append(json.dumps(
        {
            "case": "__overall__",
            "compliance_rate": _r(summary["compliance_rate"]),
            "n_units": summary["n_units"],
            "n_units_passed": summary["n_units_passed"],
            "failures_by_reason": summary["failures_by_reason"],
        },
        ensure_ascii=False,
        sort_keys=True,
    ))
    (out_dir / "compliance.metrics.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    md = [
        "# Golden-set compliance report",
        "",
        f"**Mode:** `{mode}` · **Generated:** {generated_at} · **API:** `{base_url}`",
        "",
        "This measures whether the CONDITIONS of a request survived, not how close "
        "the route came to a reference walk. A case passes only if every "
        "machine-checkable expectation of its file holds; a parity group passes "
        "only if RU and EN got the same kind of answer.",
        "",
        f"**Compliance rate: "
        f"{'n/a' if summary['compliance_rate'] is None else format(summary['compliance_rate'], '.3f')}** "
        f"({summary['n_units_passed']}/{summary['n_units']} units)",
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
            "These are recorded as unverified rather than passed or failed, "
            "because the response carries no field to decide them:",
            "",
        ]
        md += [f"- `{c}`" for c in summary["unverified_checks"]]
    (out_dir / "compliance.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def fetch_health(base_url: str, timeout: float = HEALTH_TIMEOUT_S) -> dict:
    """GET /health → dict. Raises on transport failure (caller decides)."""
    req = urllib.request.Request(
        f"{base_url}/health",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def preflight_or_exit(base_url: str) -> dict:
    """Refuse to benchmark a backend that is not there.

    Must not write a report without a live stack; exits 2 with the reason on stderr.
    """
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


def _golden_output(args) -> tuple[list[GoldenRun], list[GoldenCase], Path, dict, str]:
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


def _replay_golden_output(args) -> tuple[list[GoldenRun], list[GoldenCase], Path, dict, str]:
    snapshot_dir = Path(args.replay_golden)
    print(f"Golden replay — {snapshot_dir} (offline: no agent, no Valhalla, no clock)")
    cases = load_golden_cases(Path(args.golden_dir) if args.golden_dir else BENCH_GOLDEN)
    meta, rows = load_golden_snapshot(snapshot_dir)
    print(f"Snapshot: {len(rows)} row(s), cases {meta.get('cases')}, "
          f"git {meta.get('git_sha')}, taken {meta.get('started_at')}")
    runs = rescore_golden_rows(rows, cases, str(meta.get("base_url") or "http://replay"))
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
    write_compliance_reports(
        out_dir,
        mode=mode,
        generated_at=(
            datetime.now(UTC).isoformat()
            if mode == "golden-live"
            else str(snapshot.get("generated_at") or "replay")
        ),
        base_url=args.base_url,
        snapshot=snapshot,
        cases=cases,
        runs=runs,
        summary=summary,
    )
    print(f"  compliance report → {out_dir / 'compliance.json'}")
    print(f"  compliance report → {out_dir / 'compliance.md'}")
    print(f"  compliance JSONL  → {out_dir / 'compliance.metrics.jsonl'}")

    rate = summary["compliance_rate"]
    if args.min_compliance is not None:
        if rate is None or rate + 1e-9 < args.min_compliance:
            print(
                f"--min-compliance {args.min_compliance}: measured "
                f"{'n/a' if rate is None else f'{rate:.3f}'}",
                file=sys.stderr,
            )
            sys.exit(1)
    if args.strict and (summary["n_cases_failed"] or summary["n_parity_passed"] != summary["n_parity_groups"]):
        print(
            f"--strict: {summary['n_cases_failed']} golden case(s) failed, "
            f"{summary['n_parity_groups'] - summary['n_parity_passed']} parity group(s) failed",
            file=sys.stderr,
        )
        sys.exit(1)


def run_live(
    routes: list[GoldenRoute],
    base_url: str,
    repeat: int,
    snapshot_dir: Path | None,
    append: bool,
) -> tuple[list[list[RunRecord]], dict]:
    """Call the agent once per (case, repeat); optionally snapshot every run."""
    started_at = datetime.now(UTC).isoformat()
    run_id = _run_id()
    git_sha = _git_sha()
    meta = make_meta(
        cases=routes, base_url=base_url, repeat=repeat,
        started_at=started_at, run_id=run_id, git_sha=git_sha,
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
    request = build_request(route.query_ru, route.budget_minutes, lat, lon)
    t0 = time.monotonic()
    status: int | None = None
    raw: dict | None = None
    err: str | None = None
    try:
        status, raw = post_generate(base_url, request)
    except urllib.error.HTTPError as exc:
        status = exc.code
        err = _http_error_detail(exc)
    except urllib.error.URLError as exc:
        err = str(exc)
    latency = time.monotonic() - t0
    result = score_response(
        route, raw, http_status=status, api_error=err, latency_s=latency
    )
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
        f"maxleg={_f(r.leg.max_leg_km, 2)}km dup={r.leg.n_duplicate_stops} "
        f"unr={r.leg.n_unreachable_legs} lat={r.latency_s:.2f}s"
    )
    for f in r.failures:
        print(f"      {'✗' if f.gated else '!'} {f.kind}: {f.detail}")


def run_replay(snapshot_dir: Path) -> tuple[list[list[RunRecord]], dict, list[dict]]:
    """Recompute every metric offline from a snapshot. No clock, no RNG, no I/O.

    Reference .json files are sha256-checked against the snapshot's recorded hashes.
    """
    meta, rows = load_snapshot(snapshot_dir)
    goldens = load_golden_map()
    drift: list[dict] = []
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
            drift.append({
                "case": case,
                "recorded": recorded_sha,
                "current": current_sha,
                "note": "golden reference .json changed after the snapshot was taken",
            })
        recorded_metrics = row.get("metrics")
        result = score_response(
            golden,
            row.get("response"),
            http_status=row.get("http_status"),
            api_error=row.get("api_error"),
            latency_s=float((recorded_metrics or {}).get("latency_s") or 0.0),
        )
        if recorded_metrics and recorded_metrics != result.metrics_dict():
            drift.append({
                "case": case,
                "repeat": row.get("repeat"),
                "note": "recomputed metrics differ from the snapshot's metrics",
            })
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


def _snapshot_info(meta: dict, snapshot_dir: Path, n_rows: int) -> dict:
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


def compare_snapshots(dir_a: Path, dir_b: Path, samples: int, seed: int) -> dict:
    """Paired bootstrap A − B over the cases the two snapshots share.

    Both sides are rescored against the same current reference files, so the diff is fair.
    """
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
            paired_bootstrap(
                a_vals, b_vals, resample_indices(len(a_vals), samples, seed)
            )
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
        "a": {**_snapshot_info(meta_a, dir_a, sum(len(g) for g in groups_a)),
              "n_runs": sum(len(g) for g in groups_a),
              "n_hard_failure_runs": fails_a["n_failed_runs"],
              "n_leg_sanity_defects": fails_a["leg_sanity_defects"]},
        "b": {**_snapshot_info(meta_b, dir_b, sum(len(g) for g in groups_b)),
              "n_runs": sum(len(g) for g in groups_b),
              "n_hard_failure_runs": fails_b["n_failed_runs"],
              "n_leg_sanity_defects": fails_b["leg_sanity_defects"]},
        "shared_cases": shared_cases,
        "only_in_a": only_a,
        "only_in_b": only_b,
        "bootstrap": {"samples": samples, "seed": seed, "alpha": BOOTSTRAP_ALPHA,
                      "paired": True, "unit": "case"},
        "reference_noise_floor": REFERENCE_NOISE_FLOOR,
        "metrics": rows,
    }


def _noise_of(groups: list[list[RunRecord]], path: str) -> float | None:
    spreads = []
    for g in groups:
        vals = _attr([r.result for r in g], path)
        if len(vals) < 2:
            continue
        spreads.append(_spread(vals) or 0.0)
    return _mean(spreads)


def print_compare(cmp: dict) -> None:
    a, b = cmp["a"], cmp["b"]
    boot = cmp["bootstrap"]
    floor_of_one = 1.0 / boot["samples"]
    print()
    print("═" * 104)
    print("PAIRED BOOTSTRAP — snapshot A vs snapshot B (resampled unit: case)")
    print(f"  A: {a['dir']}  ({a['n_runs']} runs, git {a.get('git_sha')}, "
          f"{a.get('started_at')}, hard failures {a['n_hard_failure_runs']})")
    print(f"  B: {b['dir']}  ({b['n_runs']} runs, git {b.get('git_sha')}, "
          f"{b.get('started_at')}, hard failures {b['n_hard_failure_runs']})")
    print(f"  paired over {len(cmp['shared_cases'])} shared case(s): "
          f"{', '.join(cmp['shared_cases']) or '—'}")
    if cmp["only_in_a"] or cmp["only_in_b"]:
        print(f"  ⚠ only in A: {cmp['only_in_a'] or '—'} · only in B: "
              f"{cmp['only_in_b'] or '—'}")
    print(f"  B={boot['samples']}, seed={boot['seed']}, alpha={boot['alpha']}")
    print("─" * 104)
    print(f"{'metric':<30} {'n':>2} {'A':>7} {'B':>7} {'A−B':>8}  {'95% CI of Δ':>19} "
          f"{'p':>9}  {'> noise':>7}  {'noise':>6}")
    for row in cmp["metrics"]:
        n = row.get("n_paired", 0)
        if row["diff"] is None or n < 2:
            note = "n=1, no CI" if n == 1 else "n/a"
            mean_a = "n/a" if row["mean_a"] is None else f"{row['mean_a']:.3f}"
            mean_b = "n/a" if row["mean_b"] is None else f"{row['mean_b']:.3f}"
            diff = "n/a" if row["diff"] is None else f"{row['diff']:+.3f}"
            print(f"{row['label']:<30} {n:>2} {mean_a:>7} {mean_b:>7} {diff:>8}  "
                  f"{note:>19} {'n/a':>9}  {'n/a':>7}  {'n/a':>6}")
            continue
        ci = f"[{row['lo']:+.3f}, {row['hi']:+.3f}]"
        p = f"<= {floor_of_one:.4f}" if row["p"] <= floor_of_one + 1e-12 else f"{row['p']:.4f}"
        nf = "n/a" if row["noise_floor"] is None else f"{row['noise_floor']:.3f}"
        flag = "n/a" if row["exceeds_noise_floor"] is None else (
            "yes" if row["exceeds_noise_floor"] else "NO"
        )
        print(f"{row['label']:<30} {n:>2} {row['mean_a']:>7.3f} {row['mean_b']:>7.3f} "
              f"{row['diff']:>+8.3f}  {ci:>19} {p:>9}  {flag:>7}  {nf:>6}")
    print("─" * 104)
    print(f"  n = paired cases the metric is defined on (τ is n/a below "
          f"{MIN_TAU_STOPS} shared stops, so it pairs on fewer cases).")
    print(f"  reference noise floor: {cmp['reference_noise_floor']}")
    print("  a delta smaller than the noise floor is a re-run of the same pipeline,")
    print("  not a change; and a p-value on a handful of cases is a bug detector,")
    print("  not evidence of anything.")
    print("  the CI is a percentile interval, so with a handful of cases a resample")
    print("  landing exactly on 0 can leave the CI touching 0 while p counts it as")
    print("  ≤ 0: where the two disagree, believe p.")
    print("═" * 104)
    print()


def write_compare_report(cmp: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "compare.json"
    path.write_text(json.dumps(cmp, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  compare report → {path}")
    return path


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
    meta: dict
    out_dir: Path
    snapshot: dict
    generated_at: str
    drift: list[dict] = field(default_factory=list)


def _select_routes(args) -> list[GoldenRoute]:
    routes = load_golden_routes(BENCH_ROUTES)
    if args.case:
        wanted = set(args.case)
        routes = [r for r in routes if r.case in wanted]
        missing = wanted - {r.case for r in routes}
        if missing:
            print(f"No benchmark file for case(s): {sorted(missing)}", file=sys.stderr)
            sys.exit(1)
    if not routes:
        print(f"No benchmark files found in {BENCH_ROUTES}", file=sys.stderr)
        sys.exit(1)
    return routes


def _live_output(args, routes: list[GoldenRoute]) -> RunOutput:
    snapshot_dir: Path | None
    if args.snapshot is None:
        snapshot_dir = None
    elif args.snapshot:
        snapshot_dir = Path(args.snapshot)
    else:
        snapshot_dir = default_snapshot_dir()
    print("Benchmark runner — live")
    print(f"API base URL: {args.base_url}")
    print(f"Runs per case: {args.repeat}")
    print(f"Snapshot: {snapshot_dir}" if snapshot_dir else "Snapshot: off")
    print(f"Reports will be written to: {args.report_dir or BENCH_OUT}")
    preflight_or_exit(args.base_url)
    groups, meta = run_live(
        routes, args.base_url, args.repeat, snapshot_dir, args.append
    )
    n_rows = sum(len(g) for g in groups)
    return RunOutput(
        mode="live",
        groups=groups,
        meta=meta,
        out_dir=Path(args.report_dir) if args.report_dir else BENCH_OUT,
        snapshot=_snapshot_info(meta, snapshot_dir, n_rows) if snapshot_dir else {},
        generated_at=str(meta.get("started_at")),
    )


def _replay_output(args) -> RunOutput:
    snapshot_dir = Path(args.replay)
    print(f"Replay — {snapshot_dir} (no agent, no Valhalla, offline)")
    groups, meta, drift = run_replay(snapshot_dir)
    n_rows = sum(len(g) for g in groups)
    print(f"Snapshot: {n_rows} row(s), {len(groups)} case(s), "
          f"git {meta.get('git_sha')}, taken {meta.get('started_at')}")
    recorded = meta.get("golden_sha256") or {}
    if recorded:
        goldens = load_golden_map()
        drifted = [
            c for c, sha in recorded.items()
            if c in goldens and golden_sha256(goldens[c]) != sha
        ]
        if drifted:
            print(f"  ⚠ reference .json changed since the snapshot for: {drifted}")
    return RunOutput(
        mode="replay",
        groups=groups,
        meta=meta,
        out_dir=Path(args.report_dir) if args.report_dir else snapshot_dir,
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

    common = {
        "mode": out.mode,
        "generated_at": out.generated_at,
        "snapshot": out.snapshot,
        "overall": overall,
        "noise": noise,
        "failures": failures,
        "drift_rows": out.drift,
        "samples": args.bootstrap,
        "seed": args.seed,
    }
    write_json_report(out.groups, out.out_dir, per_case=per_case, **common)
    write_md_report(out.groups, out.out_dir, **common)
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

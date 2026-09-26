#!/usr/bin/env python3
# ruff: noqa: RUF001, RUF002, RUF003
#
# RUF001/RUF002/RUF003 want "ambiguous" Cyrillic letters (В~B, С~C, Р~P, О~O ...)
# spelled in Latin, and the U+2212 minus next to the U+002D hyphen. This file's
# prose is Russian, where those homoglyphs are the correct spelling of ordinary
# words, so the warnings are false positives. Kept rather than transliterating
# the comments.
"""
bench_routes.py — Golden-set evaluation for the Grodno route planner.

Three modes, one metric implementation:

  LIVE     .venv/bin/python scripts/bench_routes.py --repeat 2 --snapshot
           calls the agent, prints the tables, and (with --snapshot) appends one
           JSONL row per (case, repeat) so the run can be replayed later.

  REPLAY   .venv/bin/python scripts/bench_routes.py --replay <snapshot dir>
           recomputes EVERY metric offline from the recorded rows: no LLM, no
           Valhalla, no Postgres. Deterministic by construction — nothing in the
           replay path reads a clock, a random seed or a file outside the
           snapshot — so two replays of the same snapshot are byte-identical.
           `diff -r` them to check.

  COMPARE  .venv/bin/python scripts/bench_routes.py --compare <a> <b>
           paired bootstrap over the cases the two snapshots share, per metric,
           with a p-value and the noise floor next to every delta.

SNAPSHOT FORMAT (one JSONL file, `rows.jsonl`; first line is the run meta):

    {"record": "meta",  "schema": ..., "run_id": ..., "started_at": ...,
     "git_sha": ..., "base_url": ..., "repeat": ..., "cases": [...],
     "golden_sha256": {<case>: <sha>}, "bench_version": ...}
    {"record": "row",   "schema": ..., "case": "mir", "repeat": 1, "ts": ...,
     "request": {...the exact body sent...},
     "http_status": 200, "api_error": null,
     "candidate_ids": [...], "candidate_ids_source": "route_points",
     "response": {...raw JSON...},
     "metrics": {...}}

`candidate_ids_source` records where the ids came from. The current API does
NOT expose the pre-rerank candidate pool (see the stage-split section below), so
today the field holds the returned stop ids in walking order and the source is
"route_points". The probe is written to pick up a real pool the moment one is
exposed, so the snapshot is forward-compatible rather than hard-coded.

RETRIEVAL vs ASSEMBLY (itinerary-quality.md §4.2, retrieval-quality.md §1)

The stochastic layers are retrieval (embed + RRF + Jev rerank + Jev intent) and
assembly (matrix → optimize → Valhalla), and the assembly is deterministic given
the candidate set. Without the split, a bad number cannot be attributed. What
the API exposes today is `debug.intent_source`, `debug.constraints` and
`debug.trace` (algorithm, walk/visit seconds, per-stop categories) — the pool
produced by `retrieve()` never reaches the response, and the benchmark has no
Postgres access offline, so ids alone could not be scored anyway. So stage-1 is
measured as a PROXY and the report says so in those words:

  stage-1 (retrieval)  — for each graded reference stop, was *anything* the
      system returned within STAGE1_PROXY_RADIUS_M (250 m) of it? A place that
      the retriever never saw cannot produce a returned stop anywhere near it,
      so this is an upper bound on pool recall and a defensible "was it
      reachable at all" line. 250 m, not the 750 m matcher: at 750 m a
      reference church is "covered" by an unrelated church on the next street
      (itinerary-quality.md §1.2c).
  stage-2 (assembly)   — given the stops that stage 1 found, how good is the
      route: which of them the route actually visited (route_recall_given_pool,
      the conditional), plus precision, τ, detour, leg sanity and budget fit.

METRICS (per run; with --repeat N the cells are mean±spread over the repeats)

    recall@K      — reference stops covered by our route, greedy match within
                    MATCH_RADIUS_KM (750 m); the historical headline
    stage.recall  — stage-1 pool proxy, tight 250 m radius
    stage...|pool — stage-2 recall conditional on a stage-1 hit
    precision     — our stops that are reference places (not filler)
    kendall_tau   — τ-b of OUR order against the REFERENCE WALK order, over the
                    shared stops only; n/a below MIN_TAU_STOPS
    detour_km     — our walk − the reference walk, both haversine
    walk_diff     — (our_walk − est_walk) in minutes
    budget_fit    — does the route fit the budget the reference names
    max_leg_km    — the API's own `trace.max_leg_seconds`, converted to km at
                    the route's own average speed (trace-only, offline)

LEG SANITY + HARD FAILURES (itinerary-quality.md §1.2f, §4.5)

  * max single leg vs MAX_WALK_LEG_KM, plus the straight-line maximum as an
    offline lower bound;
  * unreachable hops via the UNREACHABLE_S sentinel (the API saturates
    `max_leg_seconds` / `walk_seconds` to 1e9 rather than shipping the matrix,
    so the count is a lower bound and is labelled as one);
  * duplicate-POI pairs inside a route's stops, under three explicit rules —
    the pipeline's own two (`DUPLICATE_RADIUS_M` regardless of name, and an
    identical normalised name within `DUPLICATE_NAME_RADIUS_M`) plus a third
    that catches the pair the 2026-09-24 report found and the pipeline's dedup
    missed: the same POI filed under two different names 745 m apart.

Hard failures are printed in their own block, OUTSIDE every score. The gated
kinds (api_error / http_status / zero_stops / unreachable_leg / geometry_missing)
also drop the run out of the quality means, per §4.5 — they are never averaged
into quality. The leg-sanity defects (duplicate_stop / leg_over_cap) stay IN the
means and are counted separately, because hiding a duplicate POI by dropping the
run would defeat the purpose of the check.

STATISTICS

Per-case means → bootstrap over the CASES (not the repeats: case-set sampling is
the dominant uncertainty, itinerary-quality.md §4.1) → mean with a 95 % CI, B =
10 000, from a seeded `random.Random` so the report is reproducible.
`--compare` uses the PAIRED bootstrap (resample the shared cases once, take the
difference) and reports a two-sided p-value. The noise floor (mean within-case
spread across repeats) is printed next to every delta, because on this pipeline
a delta smaller than it is not evidence.
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

# ── paths ────────────────────────────────────────────────────────────────────

BACKEND = Path(__file__).parent.parent.resolve()
BENCH_ROUTES = BACKEND / "benchmarks" / "routes"
BENCH_OUT = BACKEND / "benchmarks"
BENCH_SNAPSHOTS = BENCH_OUT / "snapshots"

# The routing constants below (UNREACHABLE_S, the duplicate radii, the leg cap)
# are read from the agent rather than copied, so a benchmark can never grade the
# pipeline against thresholds the pipeline does not itself use. agent/__init__
# and agent/constants import nothing, so this stays a stdlib-only script.
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from agent import constants as agent_constants  # noqa: E402

# ── harness constants ────────────────────────────────────────────────────────

UNREACHABLE_S = float(agent_constants.UNREACHABLE_S)
MAX_WALK_LEG_KM = float(agent_constants.MAX_WALK_LEG_KM)
DUPLICATE_RADIUS_M = float(agent_constants.DUPLICATE_RADIUS_M)
DUPLICATE_NAME_RADIUS_M = float(agent_constants.DUPLICATE_NAME_RADIUS_M)

# Radius of the stage-1 pool proxy. Deliberately far below MATCH_RADIUS_KM:
# 250 m is the scale at which two rows are the same physical place (the agent
# merges on DUPLICATE_RADIUS_M = 150 m), and well inside the 750 m matcher whose
# over-merging ("Троицкий костёл" credited by a neighbouring church) the
# research flagged.
STAGE1_PROXY_RADIUS_M = 250.0

# Third duplicate rule. The pipeline merges on (150 m, any name) and on (500 m,
# identical normalised name); the duplicate that reached the 2026-09-24 Grodno
# route broke both — "Костёл Обретения Святого Креста и монастырь бернардинцев"
# and "Монастырь бернардинцев и костёл Обретения Креста" are 745 m apart with
# token-Jaccard 0.86, i.e. the same building under two rows. So: same-place if
# the normalised token sets overlap by at least DUPLICATE_SIM within
# DUPLICATE_SIM_RADIUS_M. The similarity gate does the work; the radius is
# margin around the 745 m that was observed.
DUPLICATE_SIM = 0.6
DUPLICATE_SIM_RADIUS_M = 1000.0

# Reference grading (itinerary-quality.md §3.3): must-see 3, nice-to-have 1,
# available 0. The three committed cases carry no `grade` field yet, so an
# ungraded stop is treated as a must-see — the strictest reading, and the one
# that cannot inflate a score. When the graded set lands this switches by
# itself, with no code change.
GRADE_WEIGHTS = {"must-see": 3.0, "nice-to-have": 1.0, "available": 0.0}
DEFAULT_GRADE = "must-see"

# Hard failures that also GATE the run out of every quality mean (§4.5: those
# are never averaged into quality). Everything else is counted and printed
# outside the scores but stays in the means.
GATED_FAILURE_KINDS = frozenset(
    {"api_error", "http_status", "zero_stops", "unreachable_leg", "geometry_missing"}
)

BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 20260926
BOOTSTRAP_ALPHA = 0.05

REQUEST_TIMEOUT_S = 120.0

SNAPSHOT_SCHEMA = "bench_routes/snapshot/1"
ROW_SCHEMA = "bench_routes/row/1"
REPORT_SCHEMA = "bench_routes/report/2"

# The noise floor measured on this pipeline before the harness existed. Printed
# next to the computed one so a delta can be judged against a number that did
# not come out of the same run.
REFERENCE_NOISE_FLOOR = "recall 0.711 ± 0.150 over 9 runs (measured 2026-09-26)"


# ── models ──────────────────────────────────────────────────────────────────

@dataclass
class GoldenStop:
    name: str
    lat: float
    lon: float
    visit_minutes: int | None = None
    # "must-see" | "nice-to-have" | "available"; None == ungraded (see
    # GRADE_WEIGHTS).
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
    # File stem of benchmarks/routes/<case>.json; the row key and the pairing
    # key for --compare.
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

    `order` holds indices into `GoldenRoute.stops` in the order a walker should
    visit them, and `distance_km` is the haversine length of that walk.
    `article_distance_km` is the length of the raw .json order, kept only so the
    report can show how much longer the article's ordering actually is.
    """

    order: list[int]
    distance_km: float
    article_distance_km: float


@dataclass
class StageSplit:
    """Retrieval (stage 1) vs assembly (stage 2) for one run.

    `pool_exposed` is False for the current API: `retrieve()`'s output never
    reaches the response, so the pool proxy is computed over the stops the
    system returned (an upper bound on pool recall) and the report says so.
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
    max_leg_km: float | None = None            # network, estimated from the trace
    max_leg_km_straight: float | None = None   # haversine lower bound, offline
    over_cap: bool = False
    unreachable_sentinel: bool = False
    n_unreachable_legs: int = 0                # lower bound — see comment
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
class EvaluationResult:
    golden: GoldenRoute
    our_stops: list[OurStop] = field(default_factory=list)
    recall_at_k: float = 0.0
    precision: float = 0.0
    # tau-b over the shared subset only; None == n/a (fewer than MIN_TAU_STOPS
    # stops in common, i.e. there is no order left to compare).
    kendall_tau: float | None = None
    shared_stops: int = 0
    our_walk_km: float | None = None
    our_walk_km_net: float | None = None  # Valhalla network length from the API
    ref_walk_km: float | None = None
    detour_km: float | None = None
    walk_diff_min: float | None = None  # ours − est_walk
    budget_fit: bool = False
    api_error: str | None = None
    latency_s: float = 0.0
    # ── added by the freeze-and-replay harness ────────────────────────────
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


def _norm_name(name: str) -> str:
    """Lowercase, drop parentheticals and punctuation — the agent's own rule.

    Mirrors planner/pipeline.py::_norm_name so a name the agent would consider
    identical is a name this check considers identical.
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


# ── matching ────────────────────────────────────────────────────────────────

# Two stops are considered the same if they are within this radius (km)
MATCH_RADIUS_KM = 0.75  # ~750 m — generous for a pedestrian context

# recall@K is taken over the whole reference route (K = len(golden.stops)). The
# old cap of 8 mirrored a ROUTE_MAX_STOPS constant that no longer exists and
# would have let recall exceed 1.0 for longer references.


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


# ── the reference walk ───────────────────────────────────────────────────────
#
# WHY THE ORDER OF STOPS IN THE .json FILE IS NOT A REFERENCE ORDER
# =============================================================================
# Each benchmarks/routes/*.json lists its stops in the order the Wikivoyage
# article happens to present its sections. That is an editorial layout, not a
# walking route, and it is measurably a *worse* walk than the same stops visited
# in the shortest possible order (haversine km, over the committed reference
# coordinates):
#
#     route                           n   .json order   shortest order   penalty
#     Grodno: Старый город (Фарный)   5     2.804 km        1.669 km       +68 %
#     Mir: замок и исторический центр  3     1.252 km        0.795 km       +57 %
#     Новогрудок: гора и старый город  4     1.504 km        1.023 km       +47 %
#
# So the old metric — Kendall tau between our stop order and the .json order —
# was not merely noisy, it was inverted: its maximum score (+1.0) is attained by
# the *longest* walk, while a provably shortest walk scores only +0.2 / +0.33.
# The agent cannot observe how a webpage is laid out, so the only way to raise
# that number was to walk further. On the real planner every route landed on
# exactly -0.333, which is the signature of "a good route judged against an
# editorial order" (3 shared stops, 2 discordant pairs, 1 concordant).
#
# THE FIX. Both the order and the length of the reference are derived from the
# reference stops' own geometry instead of from the file layout:
#
#   1. reference walk = the shortest open Hamiltonian path over the reference
#      stops (exact bitmask DP for n <= REF_WALK_EXACT_MAX_STOPS, nearest
#      neighbour beyond). Deterministic: ties break on the lexicographically
#      smallest stop-index sequence, so the same file always yields the same
#      reference walk and the report is reproducible.
#   2. kendall_tau = tau-b between OUR order and that reference order, computed
#      ONLY over the stops present in both (matched within MATCH_RADIUS_KM).
#      Under MIN_TAU_STOPS shared stops there is no order left to correlate and
#      the metric is reported as n/a rather than as a made-up 0.0.
#   3. detour_km = our walk length - the reference walk's own length, in km.
#
# The task suggested keeping tau on the shared subset; doing that alone does NOT
# remove the artifact, because the shared subset is still *ranked* by the .json
# order — the article ordering still scores 1.0 for a 68 % longer walk. Fixing
# the *reference*, not just the *subset*, is what makes the number mean
# something. Restricting to the shared subset is still necessary and is kept:
# it stops a reference stop we never went to from dragging the correlation down
# as if we had visited it out of order.
#
# detour_km is a difference of two haversine lengths, deliberately. Our route's
# network length is available from the API (summary.length_km), but the
# reference walk has no network length — the benchmark must stay offline and
# deterministic. Subtracting a Valhalla street distance from a straight-line
# reference length would bake in the ~1.3-1.4x street-circuity factor of this
# city and make every route look like it detours. Like-for-like geometry makes
# detour_km a pure ordering+selection measurement, and the API's network length
# is reported separately as our_walk_km_net so no information is lost.

# Above this many reference stops the exact DP is skipped (2^n states); the
# reference walks in the committed set are all n <= 5.
REF_WALK_EXACT_MAX_STOPS = 12

# Fewer shared stops than this and tau-b is undefined: with 2 stops there is a
# single pair, so tau is pinned to {-1, +1} and carries no information.
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

    Exact bitmask DP (Held-Karp without the return leg) for n <=
    REF_WALK_EXACT_MAX_STOPS, nearest neighbour above that. Ties are broken on
    the lexicographically smallest index sequence, so the result is a pure
    function of the input and is stable across runs.
    """
    n = len(points)
    if n <= 1:
        return list(range(n))

    d = _pairwise_km(points)
    if n > REF_WALK_EXACT_MAX_STOPS:
        return _nearest_neighbour_order(d)

    # best[(mask, last)] = (length_km, order_tuple). Comparing the pair
    # lexicographically minimises the length first and the order second, which
    # is a consistent tie-break: two candidates reaching the same (mask, last)
    # get the same suffix appended, so the smaller prefix stays smaller.
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

    Named for what it measures (km), not for where it is used; the network
    figure is LegSanity.max_leg_km and this is its offline lower bound.
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

    `order_a` and `order_b` are sequences of element ids and may cover different
    sets of stops. Only the shared elements are ranked, and only against each
    other, so a stop missing from one side cannot contribute a spurious pair.

    Returns None (reported as n/a) when fewer than MIN_TAU_STOPS elements are
    shared: below 3 stops tau-b is pinned to {-1, +1} by a single pair, so
    publishing it would add a number without adding information.

    Both orderings are strict (no repeated ids), so there are no ties and
    tau-b collapses to tau-a = (P - Q) / n_pairs.
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

    Negative means we covered the reference stops in a tighter order than the
    reference walk itself (legitimate: our origin is chosen by the planner and
    is not part of this comparison). None when either side is unknown.
    """
    if our_distance_km is None or ref_distance_km is None:
        return None
    return our_distance_km - ref_distance_km


# ── stage 1: retrieval vs stage 2: assembly ──────────────────────────────────
#
# The split §4.2 asks for. It is only honest if the report says where the pool
# came from, so `pool_exposed` is a first-class field, not a footnote: with the
# current API the pool is NOT observable and stage-1 is a proximity proxy over
# the stops the system returned.

# Probe order. Any of these carrying the candidate list is the real pre-rerank
# pool; a list of objects with lat/lon is enough to score it offline, a list of
# bare ids is recorded but not scoreable (the benchmark has no places table).
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

    Returns {"path", "ids", "points"} or None. A pool of bare ids is recorded
    but carries no coordinates, so it cannot be scored offline; the report says
    so rather than pretending an id list is a measurement.
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

    Not a one-to-one assignment. Two reference stops can legitimately be served
    by one returned stop at this radius, and the question stage 1 answers is
    "was this place reachable at all", not "was it matched bijectively".
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
        # A real pool with coordinates: score it directly.
        points = pool["points"]
        source = "stage1_pool"
        exposed = True
    else:
        # Fall back to what the response actually carries. The returned stops
        # are a subset of the pool the assembly saw, so this is an UPPER bound
        # on pool recall — stated in the report, never hidden.
        points = [(s.lat, s.lon) for s in our_stops]
        source = "route_points"
        exposed = False

    covered = _any_within(golden.stops, points, STAGE1_PROXY_RADIUS_M)
    n_cov = sum(covered)
    total_w = golden.total_weight
    cov_w = sum(s.weight for s, c in zip(golden.stops, covered, strict=True) if c)
    recall = (n_cov / len(golden.stops)) if golden.stops else 0.0
    recall_w = (cov_w / total_w) if total_w > 0 else 0.0

    # Stage 2, conditional on stage 1: of the reference stops the proxy says
    # were reachable, how many did the route actually visit? This is the number
    # that separates "the retriever never found the castle" from "it found the
    # castle and the optimiser left it out".
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


# ── leg sanity ───────────────────────────────────────────────────────────────

def find_duplicate_stop_pairs(our_stops: list[OurStop]) -> list[dict]:
    """Pairs of returned stops that are the same physical POI, in visit order.

    Three explicit rules, first match wins, so the label says WHY:

      coincident    within DUPLICATE_RADIUS_M, any name — the pipeline's own rule
      same_name     identical normalised name within DUPLICATE_NAME_RADIUS_M —
                    the pipeline's own rule
      same_name_near  token overlap >= DUPLICATE_SIM within DUPLICATE_SIM_RADIUS_M
                    — the rule the pipeline lacks, and the one that catches the
                    "бернардинцев" pair the 2026-09-24 report found
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

    `max_leg_seconds` is the API's own trace field, so the leg check reads the
    router's verdict rather than a second guess. The km figure converts it at
    the route's own average speed, and the straight-line maximum is kept as an
    offline lower bound for snapshots that have no trace at all.

    UNREACHABLE_S (1e9) is MISSING DATA, not a cost: a route whose trace is
    saturated contains at least one hop Valhalla could not connect, and the
    exact count is not recoverable offline because the matrix is not shipped —
    so `n_unreachable_legs` is documented as a lower bound, never a count of
    unconnected hops.
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

    # Under-detect rather than over-detect: the straight line is the network's
    # lower bound, so it can only fire when the network leg is definitely too
    # long.
    for value in (leg.max_leg_km, leg.max_leg_km_straight):
        if value is not None and value > MAX_WALK_LEG_KM:
            leg.over_cap = True
            break

    leg.duplicate_pairs = find_duplicate_stop_pairs(our_stops)
    leg.n_duplicate_stops = len(leg.duplicate_pairs)
    # The API answers with an empty shape when Valhalla could not draw the tour
    # (pipeline.py::_render_tour) — the UI then shows every point with no line
    # between them. Only a response that actually carries an empty `shape` counts;
    # a response with no shape key at all is an older/partial response.
    leg.geometry_missing = "shape" in (raw or {}) and not raw.get("shape")
    return leg


def _saturated(value) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return not math.isfinite(float(value)) or float(value) >= UNREACHABLE_S


# ── API call ─────────────────────────────────────────────────────────────────

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


# ── evaluation ──────────────────────────────────────────────────────────────

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

    The single scoring function: the live path, the snapshot path and `--replay`
    all go through it, so a replayed number cannot drift from a live number.
    Pure — no clock, no RNG, no I/O.
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

    Rebuilt per run rather than cached on the route: it is a pure function of
    the committed coordinates, so this is cheap and keeps the scorer usable as a
    single self-contained call in the tests.
    """
    golden = result.golden
    ref = build_reference_walk(golden.stops)
    result.ref_walk_km = ref.distance_km

    # Element ids are positions in our_stops. `ref_shared` lists the matched
    # stops in reference-walk order; `our_shared` lists the same stops in the
    # order our route actually walks them (our_stops is already in visit
    # order, so sorting by index gives it). kendall_tau intersects the two
    # itself and returns None below MIN_TAU_STOPS.
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


# ── statistics ───────────────────────────────────────────────────────────────
#
# Plain Python on purpose: numpy/scipy are not dependencies of this repo and a
# 10k bootstrap over a few dozen cases costs milliseconds. The RNG is seeded, so
# two replays of one snapshot print the same CIs to the last digit.

def percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile of an already sorted list (clamped)."""
    if not sorted_values:
        return float("nan")
    k = min(len(sorted_values) - 1, max(0, int(q * len(sorted_values))))
    return sorted_values[k]


def resample_indices(n: int, samples: int, seed: int) -> list[tuple[int, ...]]:
    """`samples` bootstrap resamples of `n` case indices, from a seeded RNG.

    Cached per (n, samples, seed) so every metric in one report is resampled
    with the SAME indices — the pairing is what makes the per-metric CIs
    comparable to each other and to a --compare run.
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

    Resample the case indices once per iteration and take the difference on that
    resample, so query difficulty — the dominant shared component — cancels
    instead of being counted twice. The p-value is the two-sided bootstrap
    fraction: how often a resample puts the difference on the other side of 0.

    A single paired case gets a difference and NO interval and NO p-value.
    Resampling one case always reproduces that case, which would print a
    degenerate CI that excludes 0 next to p = 1.0 — a "significant" number that
    means nothing. Say n/a instead.
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


# ── snapshot I/O ─────────────────────────────────────────────────────────────

def default_snapshot_dir() -> Path:
    return BENCH_SNAPSHOTS / datetime.now(UTC).strftime("%Y-%m-%d")


def _git_sha() -> str:
    """Short HEAD sha, or "unknown" outside a checkout (never fatal)."""
    try:
        out = subprocess.run(  # fixed argv, no shell, no user input
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

    Only `rows*.jsonl` is snapshot data. A replay run writes report.json,
    report.md and report.metrics.jsonl NEXT TO the snapshot by default, and
    report.metrics.jsonl also carries a `case` key — globbing every *.jsonl
    would make the second replay of a snapshot consume the first one's report
    as input. Unknown record kinds are dropped for the same reason.
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


# ── aggregation over --repeat runs ───────────────────────────────────────────

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

    Gated hard failures are SKIPPED, and so are runs the metric does not define
    (None). A 422 and a route-with-zero-stops both score 0.0 recall; averaging
    those in would be averaging a failure into quality, which is exactly what
    §4.5 forbids. The per-case table shows the hard-failure count next to the
    mean so the exclusion is visible rather than silent.
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


# ── the metric set the CI table reports ──────────────────────────────────────
# (attribute path, label, decimals). Everything here is a quality number; the
# hard-failure block deliberately has no entry here.

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


# ── per-case / overall statistics over a run set ─────────────────────────────

def _results(groups: list[list[RunRecord]]) -> list[EvaluationResult]:
    return [rec.result for g in groups for rec in g]


def per_case_scores(groups: list[list[RunRecord]]) -> dict[str, dict[str, float | None]]:
    """One score per case per metric: the mean over that case's defined runs.

    The case is the resampling unit for every CI and every paired bootstrap, so
    this dict is the single definition of "a case's score" in the whole script.
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

    Reported next to every delta: a change smaller than this is indistinguishable
    from re-running the same pipeline. (The topic/case-sampling variance is the
    other half of the picture and is what the bootstrap CI over cases captures.)
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


# ── formatting helpers ───────────────────────────────────────────────────────

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

    Under the route-points fallback the pool IS the route, so the conditional
    "did the route visit what stage 1 found?" is vacuously yes. Printed as a
    caveat so a 1.000 in the table cannot be read as a quality result.
    """
    stats = overall.get("stage.route_recall_given_pool", {})
    return stats.get("mean") is not None and stats["mean"] >= 1.0


# ── ASCII tables ─────────────────────────────────────────────────────────────

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
        # Errors go under the row, never as a column: a column would make the
        # table non-rectangular and the failure list is already printed, in full,
        # in the hard-failure block below.
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


# ── report writers ───────────────────────────────────────────────────────────

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


# ── modes ───────────────────────────────────────────────────────────────────

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

    The only file reads are the snapshot itself and the reference .json files
    (whose sha256 is checked against the value recorded at snapshot time, so a
    golden that moved under us is reported instead of silently rescored).
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


# ── compare ──────────────────────────────────────────────────────────────────

def compare_snapshots(dir_a: Path, dir_b: Path, samples: int, seed: int) -> dict:
    """Paired bootstrap A − B over the cases the two snapshots share.

    Both sides are rescored from their own recorded responses against the SAME
    (current) reference files, so a reference edit cannot masquerade as a
    pipeline difference.
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


# ── CLI ──────────────────────────────────────────────────────────────────────

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
             "(default: backend/benchmarks, or the snapshot dir under --replay)",
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
            "With no value: backend/benchmarks/snapshots/<date>."
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
        "--strict",
        action="store_true",
        help="Exit 1 when any hard failure was recorded (CI gate)",
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

    if args.compare:
        dir_a, dir_b = (Path(p) for p in args.compare)
        cmp = compare_snapshots(dir_a, dir_b, args.bootstrap, args.seed)
        print_compare(cmp)
        out_dir = Path(args.report_dir) if args.report_dir else BENCH_OUT
        write_compare_report(cmp, out_dir)
        return

    out = _replay_output(args) if args.replay else _live_output(args, _select_routes(args))
    _emit(args, out)


if __name__ == "__main__":
    main()

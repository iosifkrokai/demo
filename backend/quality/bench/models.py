"""The records a case, a run and a report are built from."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import constants
from quality.bench.config import (
    BOOTSTRAP_SAMPLES,
    BOOTSTRAP_SEED,
    DEFAULT_GRADE,
    GATED_FAILURE_KINDS,
    GRADE_WEIGHTS,
    STAGE1_PROXY_RADIUS_M,
    _r,
)


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
    profile: str | None = None
    origin: tuple[float, float] | None = None
    fingerprint: str | None = None
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
    """The benchmark's reference walk, derived from the reference stop geometry."""

    order: list[int]
    distance_km: float
    article_distance_km: float


@dataclass
class StageSplit:
    """Retrieval (stage 1) vs assembly (stage 2) for one run."""

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
    cap_km: float | None = None
    over_cap: bool = False
    unreachable_sentinel: bool = False
    n_unreachable_legs: int = 0
    duplicate_pairs: list[dict[str, Any]] = field(default_factory=list)
    n_duplicate_stops: int = 0
    n_legitimate_adjacent: int = 0
    geometry_missing: bool = False


@dataclass
class Failure:
    kind: str
    detail: str

    @property
    def gated(self) -> bool:
        return self.kind in GATED_FAILURE_KINDS

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail, "gated": self.gated}


@dataclass
class GoldenCase:
    """One requirement-compliance case from quality/cases/compliance/*.json."""

    id: str
    locale: str
    query: str
    filters: dict[str, Any]
    expectations: dict[str, Any]
    parity_group: str | None = None
    path: Path | None = None


@dataclass
class ComplianceVerdict:
    """PASS/FAIL for one golden case (or parity group) against a plan response."""

    case_id: str
    passed: bool
    reason: str
    detail: str = ""
    status: str | None = None
    status_source: str | None = None
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def failed_checks(self) -> list[str]:
        return [k for k, v in self.checks.items() if v.get("ok") is False]

    @property
    def unverified_checks(self) -> list[str]:
        return [k for k, v in self.checks.items() if v.get("unverified")]

    def as_dict(self) -> dict[str, Any]:
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
    stage: StageSplit = field(
        default_factory=lambda: StageSplit(
            False, "none", 0, STAGE1_PROXY_RADIUS_M, 0, 0.0, 0, 0.0, 0.0, 0.0, None, 0
        )
    )
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

    def metrics_dict(self) -> dict[str, Any]:
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


@dataclass(frozen=True)
class ReportInput:
    """One run's identity and numbers — what every report writer is handed."""

    mode: str
    generated_at: str
    snapshot: dict[str, Any]
    cases_dir: str = ""
    base_url: str = ""
    overall: dict[str, Any] = field(default_factory=dict)
    noise: dict[str, Any] = field(default_factory=dict)
    failures: dict[str, Any] = field(default_factory=dict)
    drift_rows: list[dict[str, Any]] = field(default_factory=list)
    samples: int = BOOTSTRAP_SAMPLES
    seed: int = BOOTSTRAP_SEED


@dataclass
class RunRecord:
    """One (case, repeat) run — the unit the snapshot stores and the report reads."""

    case: str
    case_name: str
    repeat: int
    result: EvaluationResult
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    ts: str | None = None
    git_sha: str | None = None
    recorded_metrics: dict[str, Any] | None = None


_R = constants.EARTH_RADIUS_KM

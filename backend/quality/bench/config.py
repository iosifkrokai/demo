"""Constants, paths and the category vocabulary the harness reads."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import get_args

BACKEND = Path(__file__).resolve().parents[2]
QUALITY = BACKEND / "quality"
BENCH_ROUTES = QUALITY / "cases" / "routes"
BENCH_GOLDEN = QUALITY / "cases" / "compliance"
BENCH_CURATED = QUALITY / "cases" / "curated"
BENCH_CURATED_OUT = QUALITY / "reports" / "curated"
DUPLICATE_EXCEPTIONS = QUALITY / "cases" / "duplicate_exceptions.json"
BENCH_OUT = QUALITY / "reports"
BENCH_SNAPSHOTS = BENCH_OUT / "snapshots"

STATUSES = frozenset({"ready", "degraded", "pending", "infeasible"})

CHECK_API_ERROR = "api_error"
CHECK_WRONG_STATUS = "wrong_status"
CHECK_TOO_FEW_PLACES = "too_few_places"
CHECK_MISSING_MANDATORY_CATEGORY = "missing_mandatory_category"
CHECK_FORBIDDEN_CATEGORY_PRESENT = "forbidden_category_present"
CHECK_MISSING_NAMED_PLACE = "missing_named_place"
CHECK_REQUIREMENT_UNMET = "requirement_unmet"
CHECK_REQUIREMENT_STATUS = "requirement_status"
CHECK_MOMENT_APPLIED = "moment_not_applied"
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
    CHECK_REQUIREMENT_UNMET,
    CHECK_REQUIREMENT_STATUS,
    CHECK_MOMENT_APPLIED,
    CHECK_OUT_OF_REGION_POINT,
    CHECK_OVER_BUDGET,
    CHECK_RESULT_MODE,
)

CANONICAL_CATEGORIES: dict[str, str] = {}
CANONICAL_CODES_NORM: frozenset[str] = frozenset()


if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import constants as agent_constants  # noqa: E402
from db.seed.taxonomy import all_categories  # noqa: E402
from routing.models import (  # noqa: E402
    RequirementKind,
    RequirementStatus,
)

CANONICAL_CATEGORIES = {
    canonical.code.strip().lower().replace("ё", "е"): canonical.code
    for canonical in all_categories()
}
CANONICAL_CODES_NORM = frozenset(CANONICAL_CATEGORIES)


UNREACHABLE_S = float(agent_constants.UNREACHABLE_S)
DUPLICATE_RADIUS_M = float(agent_constants.DUPLICATE_RADIUS_M)
LEG_SPEED_KMH = {
    "pedestrian": float(agent_constants.PEDESTRIAN_SPEED_KMH),
    "bicycle": float(agent_constants.BICYCLE_SPEED_KMH),
    "auto": float(agent_constants.AUTO_SPEED_KMH),
}
DUPLICATE_NAME_RADIUS_M = float(agent_constants.DUPLICATE_NAME_RADIUS_M)
REQUIREMENT_KINDS = frozenset(get_args(RequirementKind))
REQUIREMENT_STATUSES = frozenset(get_args(RequirementStatus))

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


_PAIR = 2
_QUERY_MIN_CHARS = 3
_QUERY_MAX_CHARS = 500
_PARTY_CHILDREN_MAX = 20


def _r(value, digits: int = 6):
    """Round a float for the JSONL so a diff of two snapshots is readable."""
    if isinstance(value, float) and not isinstance(value, bool):
        return round(value, digits)
    if isinstance(value, dict):
        return {k: _r(v, digits) for k, v in value.items()}
    if isinstance(value, list):
        return [_r(v, digits) for v in value]
    return value


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)

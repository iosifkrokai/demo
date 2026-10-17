#!/usr/bin/env python3
"""Golden-set evaluation for the Grodno route planner."""

from __future__ import annotations

from quality.bench.analysis import (
    _leg_failures,
    duplicate_exceptions,
    find_duplicate_stop_pairs,
    haversine_km,
    leg_cap_km,
    leg_sanity,
    norm_name,
)
from quality.bench.cases import (
    _check_curated_case,
    _filter_moment,
    load_curated_routes,
    load_golden_cases,
    load_golden_routes,
)
from quality.bench.cli import _report_dir, main
from quality.bench.compliance import _moment_check, _requirement_status_check
from quality.bench.config import (
    BENCH_CURATED,
    BENCH_CURATED_OUT,
    BENCH_ROUTES,
    STAGE1_PROXY_RADIUS_M,
)
from quality.bench.models import OurStop

__all__ = [
    "BENCH_CURATED",
    "BENCH_CURATED_OUT",
    "BENCH_ROUTES",
    "STAGE1_PROXY_RADIUS_M",
    "OurStop",
    "_check_curated_case",
    "_filter_moment",
    "_leg_failures",
    "_moment_check",
    "_report_dir",
    "_requirement_status_check",
    "duplicate_exceptions",
    "find_duplicate_stop_pairs",
    "haversine_km",
    "leg_cap_km",
    "leg_sanity",
    "load_curated_routes",
    "load_golden_cases",
    "load_golden_routes",
    "main",
    "norm_name",
]

if __name__ == "__main__":
    raise SystemExit(main())

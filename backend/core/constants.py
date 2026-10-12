"""Domain constants — models, weights, thresholds, limits.

Deliberate engineering choices that belong in reviewable code, not a .env file.
"""

from __future__ import annotations

# A refinement is a delta on a route the tourist already holds: it must feel fast.
REFINE_DEADLINE_S = 40.0
# A fresh generation has no constant here on purpose: its ceiling lives in the
# runner (`agent.runner.WALL_CLOCK_TIMEOUT_S`), which clamps whatever the caller
# passes to it. A second number here would only be clamped away again.
COST_MATRIX_MIN_LEFT_S = 26.0
POOL_TRIM_SIZE = 30
VALHALLA_ORDER_MIN_LEFT_S = 18.0
VALHALLA_ORDER_TIMEOUT_S = 10.0
VALHALLA_ORDER_RETRIES = 0
RENDER_MIN_LEFT_S = 6.0


RRF_K = 60
MMR_LAMBDA = 0.7

CONVENIENCE_RADIUS_M = 500
CONVENIENCE_MAX_ADDED = 6
VALHALLA_MAX_LOCATIONS = 20

NEGATIVE_FILTER_ENABLED = True
RETRIEVAL_POOL_SIZE = 50
MMR_POOL_SIZE = 30


GEO_FOCUS_KM = 12.0
GEO_FOCUS_DISCOVERY_MAX_KM = 36.0

WALK_TOO_FAR_KM = 15.0
WALK_TOO_LONG_MINUTES = 240

UNREACHABLE_S = 10**9

UNKNOWN_S = float("nan")


MIN_BUDGET_MIN = 15
MAX_BUDGET_MIN = 480

VALHALLA_TIMEOUT_S = 20.0
VALHALLA_MAX_RETRIES = 2

GRODNO_BBOX = {"south": 52.75, "west": 23.35, "north": 54.80, "east": 27.00}

MAX_WALK_LEG_KM = 5.0
DUPLICATE_RADIUS_M = 150.0
DUPLICATE_NAME_RADIUS_M = 500.0

WALK_LEG_BUDGET_SHARE = 0.25
NAME_MATCH_MIN_SIM = 0.3

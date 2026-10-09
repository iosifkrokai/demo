"""Domain constants — models, weights, thresholds, limits.

NOT environment variables. These are deliberate engineering choices that
belong in reviewable code, not in a .env file. Environment carries only
secrets (API keys) and deployment addresses (DSN, service URLs, ports) —
see config.py.

Tuning here is a code review decision: change → tests → commit.
"""

from __future__ import annotations

from . import taxonomy

# ── Per-request deadlines ───────────────────────────────────────────────────
# One route request has a hard end-to-end budget.  Exceeding it is never a
# hang: the planner drops the optional work (Valhalla re-ordering, geometry)
# and returns a smaller plan with an honest status instead.
#
# Kept generous on purpose. With 40 the re-ordering was usually thrown away for
# want of a second or two (interpretation + rerank + cost matrix took ~23 s,
# leaving 17.2 s against a threshold of 18), and the plan came back with two stops
# that followed the retrieval order rather than a walkable one. The interpretation
# layer alone is now allowed 90 s for a deep model (a thinking model measured
# 20-23 s per call), so a 75 s end-to-end ceiling would have guaranteed that the
# better reading it just paid for was discarded. The ceiling exists to bound the
# worst case, not to discard the step that makes the route good.
# INVARIANT: every timeout in front of this one must be larger, or the front door
# becomes the limit and a slow-but-valid plan dies as a 504 the tourist reads as
# «всё сломалось». Dependents: frontend/nginx.conf proxy_read/send_timeout (300 s)
# and scripts/bench_routes.py REQUEST_TIMEOUT_S (300 s). Raise them together.
REQUEST_DEADLINE_S = 240.0
# Less than this left → trim the candidate pool before the (50×50) cost matrix.
COST_MATRIX_MIN_LEFT_S = 26.0
POOL_TRIM_SIZE = 30
# Less than this left → do not ask Valhalla to re-order a wide tour; its
# `optimized_route` call is unbounded over a region and was the 60 s+ tail.
VALHALLA_ORDER_MIN_LEFT_S = 18.0
# Less than this left → skip the geometry call: the plan is returned with
# `geometry_missing`, which the verifier reports as `degraded`.
RENDER_MIN_LEFT_S = 6.0

# ── ML models (OpenRouter) ──────────────────────────────────────────────────
# The interpretation model is a deployment fact chosen by measurement, not a
# tuning knob: it lives in planner/agent_interpret.py (DEFAULT_MODEL) next to
# the agent that uses it, and is overridable per-process with
# AGENT_INTERPRET_MODEL so a benchmark can pick one.

# The embedding model is no longer a constant here: it lives with the local
# embedder in agent/embeddings.py (MODEL_NAME / EMBED_DIM).

# ── Intent taxonomy ─────────────────────────────────────────────────────────
INTENT_TYPES = ("discovery", "specific", "themed", "vague")

# Category taxonomy — the canonical codes live in data/taxonomy.csv and are
# read through agent/taxonomy.py. Nothing here is a second list: import a code
# from taxonomy instead of adding one to this tuple.
CATEGORIES = taxonomy.all_codes()

# ── Retrieval / ranking ─────────────────────────────────────────────────────
# Reciprocal Rank Fusion constant (standard 60 — Cormack et al.).
RRF_K = 60
# MMR relevance/diversity trade-off (0=pure diversity, 1=pure relevance).
MMR_LAMBDA = 0.7

# Everyday stops a walk needs ("добавь кофейню и туалет"): these are picked by
# PROXIMITY to the route, not by relevance — a coffee 12 km away is not a stop.
# Defined as "every service-role category" so a new service code is added once,
# in taxonomy.csv, and appears here automatically.
CONVENIENCE_CATEGORIES = tuple(
    cat.code for cat in taxonomy.all_categories() if cat.role == "service"
)
CONVENIENCE_RADIUS_M = 500      # how far off the route a convenience stop may be
CONVENIENCE_MAX_ADDED = 6       # at most this many convenience stops per refine
# Valhalla rejects more than 20 locations per /route and /optimized_route call
# (error_code 150). Keep the pool it sees inside that (the webapp chunks its own
# drawing call, but the planner's ordering call is single-shot).
VALHALLA_MAX_LOCATIONS = 20

NEGATIVE_FILTER_ENABLED = True  # drop candidates matching forbidden categories
RETRIEVAL_POOL_SIZE = 50   # candidates after RRF fusion
# Final pool handed to the optimizer. Raised from 12: measured live, a 120-minute
# walk in central Grodno came back as 2 stops / 20 minutes of walking because the
# optimizer only ever saw 9 candidates of which 3 were sights — no model can build
# a full walk out of that. This ceiling applies to the *budgeted* path, where MMR
# trims to this size; RETRIEVAL_POOL_SIZE stays at 50 on purpose (see
# frontend sidebar): with no budget MMR does not trim at all, so a larger fusion
# pool would send every candidate to a matrix that grows into hours.
MMR_POOL_SIZE = 30

# A place whose visit time exceeds a share of the total budget is dropped before
# matrix computation (it cannot fit alongside anything else). That share lives
# in exactly one place — `planner/cost.VISIT_CAP_BUDGET_SHARE` — because it is
# applied where the cost matrix is built; a second copy here was dead and could
# only drift.

# Keep the route walkable: max distance between the anchor and candidates.
# Keyword retrieval can fuse places across the whole voblast otherwise.
GEO_FOCUS_KM = 12.0
# Discovery query with no anchor/named place at all: the route still has to be
# walkable, so the pool stays local. Expanding the focus to Valhalla's matrix
# cap (200 km) used to mix stops hundreds of kilometres apart and the optimizer
# then answered 422 "optimizer could not produce a route with ≥ 2 stops".
GEO_FOCUS_DISCOVERY_MAX_KM = 36.0

# ── Walkability of the answer ───────────────────────────────────────────────
# The profile the tourist chose has to stay believable. «Все костёлы Гродненской
# области» under `pedestrian` measured a 17-hour, 211-km tour: an honest answer
# that nobody can walk. Past these bounds the plan is still returned — the request
# really did ask for the region, and a silently trimmed dozen would lie about it —
# but the answer also names the ways to actually make the trip.
WALK_TOO_FAR_KM = 15.0
WALK_TOO_LONG_MINUTES = 240

# ── Unreachable pairs ───────────────────────────────────────────────────────
# A pair Valhalla cannot connect (500 "Could not find candidate edge used for
# label" at every snap radius — e.g. the Grodno-fortress POI at
# 53.597305,23.800828, which has no pedestrian edges anywhere near it) is marked
# with this many seconds. It is deliberately finite: budget/leg comparisons treat
# it as unreachable, while int(inf) would raise OverflowError in the optimizer.
UNREACHABLE_S = 10**9

# ── Route optimizer ─────────────────────────────────────────────────────────
# (No stop cap here: the only limits are the user's time budget and transport.
#  The old ROUTE_MAX_STOPS=8 silently cut routes short.)

# ── Time budget bounds (minutes) ────────────────────────────────────────────
MIN_BUDGET_MIN = 15
MAX_BUDGET_MIN = 480

# ── Valhalla ────────────────────────────────────────────────────────────────
VALHALLA_TIMEOUT_S = 20.0
VALHALLA_MAX_RETRIES = 2

# Grodno voblast bbox (OSM relation 59173, generous) — osmium order W,S,E,N.
GRODNO_BBOX = {"south": 52.75, "west": 23.35, "north": 54.80, "east": 27.00}

# ── Visit-time defaults by category (minutes) ───────────────────────────────
# Derived from the taxonomy so a category's visit time is defined in exactly
# one place (data/taxonomy.csv); taxonomy.visit_minutes() answers for unknown or
# legacy free-text categories, so there is no second default to drift.
VISIT_TIME_BY_CATEGORY: dict[str, int] = {
    cat.code: cat.visit_minutes for cat in taxonomy.all_categories()
}

# ── Walkability ──────────────────────────────────────────────────────────────
# A pedestrian route is only useful if every leg is walkable.
# MAX_WALK_LEG_KM — hard cap: a single leg longer than this is not acceptable.
#   At 4 km/h, 2 km ≈ 30 min (WALK_LEG_BUDGET_SHARE of a 2-hour budget).
#   We set the absolute ceiling at 5 km (≈75 min walk) to cover edge cases.
MAX_WALK_LEG_KM = 5.0
# Two rows closer than this are the same physical POI (a curated row plus an
# OSM row with a different name) — keep only the best-ranked one per cluster.
DUPLICATE_RADIUS_M = 150.0
# Same again, but for rows carrying an identical normalised name yet imprecise
# coordinates (a curated row and its OSM twin can sit a few hundred metres
# apart).  Only an exact normalised-name match merges at this distance.
DUPLICATE_NAME_RADIUS_M = 500.0

# A leg longer than this share of the total budget is rejected (unless must-visit).
WALK_LEG_BUDGET_SHARE = 0.25
# Name-match similarity threshold for must_visit resolution.
# A named-place token is promoted to must_visit only when the pg_trgm
# similarity against the POI name (not town/district) exceeds this value.
# 0.3 is conservative: it captures "Мирскому" → "Мирский замок" (substring
# overlap) while filtering out generic tokens like "Гродно" matching any
# Grodno POI by town name.
NAME_MATCH_MIN_SIM = 0.3

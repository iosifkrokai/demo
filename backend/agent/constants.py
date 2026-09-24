"""Domain constants — models, weights, thresholds, limits.

NOT environment variables. These are deliberate engineering choices that
belong in reviewable code, not in a .env file. Environment carries only
secrets (API keys) and deployment addresses (DSN, service URLs, ports) —
see config.py.

Tuning here is a code review decision: change → tests → commit.
"""

from __future__ import annotations

# ── ML models (OpenRouter) ──────────────────────────────────────────────────
# Jev: TypeSafe System One decision model — typed answers (noul/choice/score),
# no text generation. Used for intent classification and rerank scoring.
JEV_MODEL = "typesafe/jev-1.13"
JEV_TIMEOUT_S = 5.0

# Text-embedding model for the vector retrieval signal.
EMBED_MODEL = "openai/text-embedding-3-small"
EMBED_DIM = 1536

# ── Intent taxonomy ─────────────────────────────────────────────────────────
INTENT_TYPES = ("discovery", "specific", "themed", "vague")
ERA_HINTS = ("any", "pre1900", "soviet", "modern")
PARTY_TYPES = ("solo", "family", "couple", "group")

# Category taxonomy — mirrors data/*.csv `category` column and the
# visit-time table in planner/cost.py.
CATEGORIES = (
    "замок", "костёл", "церковь", "монастырь", "дворец", "усадьба",
    "парк", "музей", "памятник", "храм", "архитектура",
    "инфраструктура", "кладбище",
)

# ── Retrieval / ranking ─────────────────────────────────────────────────────
# Reciprocal Rank Fusion constant (standard 60 — Cormack et al.).
RRF_K = 60
# MMR relevance/diversity trade-off (0=pure diversity, 1=pure relevance).
MMR_LAMBDA = 0.7

NEGATIVE_FILTER_ENABLED = True  # drop candidates matching forbidden categories
RETRIEVAL_POOL_SIZE = 50   # candidates after RRF fusion
RERANK_POOL_SIZE = 30      # candidates sent to Jev scoring
MMR_POOL_SIZE = 12         # final candidate pool for the optimizer

# A place whose visit time exceeds this share of the total budget is dropped
# before matrix computation (can't fit alongside anything else).
MAX_VISIT_BUDGET_SHARE = 0.4

# Keep the route walkable: max distance between the anchor and candidates.
# Keyword retrieval can fuse places across the whole voblast otherwise.
GEO_FOCUS_KM = 12.0
GEO_FOCUS_MAX_KM = 200.0   # cap: Valhalla's matrix limit

# ── Route optimizer ─────────────────────────────────────────────────────────
ROUTE_MAX_STOPS = 8

# ── Time budget bounds (minutes) ────────────────────────────────────────────
MIN_BUDGET_MIN = 15
MAX_BUDGET_MIN = 480
DEFAULT_BUDGET_MIN = 120

# ── Valhalla ────────────────────────────────────────────────────────────────
VALHALLA_TIMEOUT_S = 20.0
VALHALLA_MAX_RETRIES = 2

# Grodno voblast bbox (OSM relation 59173, generous) — osmium order W,S,E,N.
GRODNO_BBOX = {"south": 52.75, "west": 23.35, "north": 54.80, "east": 27.00}

# ── Visit-time defaults by category (minutes) ───────────────────────────────
VISIT_TIME_BY_CATEGORY: dict[str, int] = {
    "замок": 40, "музей": 40, "монастырь": 30,
    "дворец": 30, "усадьба": 30, "парк": 30,
    "костёл": 20, "церковь": 20, "храм": 20,
    "архитектура": 20, "кладбище": 15,
    "памятник": 10, "инфраструктура": 10,
}
VISIT_TIME_DEFAULT = 15

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

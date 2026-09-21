"""FastAPI agent: turns free-text Russian queries into pedestrian walking routes.

Endpoints:
    POST /routes/generate   body: {"query": str, "n_points"?: int}
                            -> {points: [...], shape: GeoJSON LineString, summary: {...}}
    POST /routes/reroute    body: {"point_ids": [int]}
                            -> {points: [...], shape, summary}

Local fastembed model is loaded once at startup (lifespan handler) and held in
state["embedder"]. Same model is used by the DB-load step (enrich_places.py).
The e5 family requires a "query: " prefix on queries; passages get "passage: " in enrich.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastembed import TextEmbedding
from pydantic import BaseModel, Field

from .llm import init as init_llm, parse_query
from .search import candidates_by_embedding, db_categories, fetch_points_by_ids
from .valhalla_client import route_through

DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")
VALHALLA_URL = os.environ.get("VALHALLA_URL", "http://localhost:8002")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    print(f"loading {EMBED_MODEL} via fastembed (one-time)...")
    state["embedder"] = TextEmbedding(EMBED_MODEL)
    init_llm()  # local GGUF via llama-cpp-python; blocking first-time download
    state["db"] = psycopg.connect(DSN, autocommit=True)
    yield
    state["db"].close()


app = FastAPI(title="grodno-poc-agent", lifespan=lifespan)

# The webapp (port 80) and the agent (port 8080) are different origins, so the
# browser fires a CORS preflight before fetch(). Locking origins to localhost
# is fine for the POC.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://localhost:80", "http://127.0.0.1"],
    allow_credentials=False,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


class GenerateReq(BaseModel):
    query: str
    n_points: int = Field(default=4, ge=2, le=10)
    time_budget_minutes: int | None = Field(default=None, ge=15, le=600)


class RerouteReq(BaseModel):
    point_ids: list[int]


# Visit time per category, minutes. Used to fit a route into a time budget:
# walking time (Valhalla summary) + visit time at each stop must not exceed it.
VISIT_TIME_MIN: dict[str, int] = {
    "замок": 40,
    "музей": 40,
    "монастырь": 30,
    "дворец": 30,
    "усадьба": 30,
    "парк": 30,
    "костёл": 20,
    "церковь": 20,
    "храм": 20,
    "городище": 20,
    "сквер": 15,
    "памятник": 10,
    "монумент": 10,
}
_DEFAULT_VISIT_TIME = 15


def visit_time(category: str | None) -> int:
    """Estimated minutes to look around a place of a given category."""
    if not category:
        return _DEFAULT_VISIT_TIME
    c = category.lower()
    if c in VISIT_TIME_MIN:
        return VISIT_TIME_MIN[c]
    # compound / verbose categories — substring match
    for key, minutes in VISIT_TIME_MIN.items():
        if key in c:
            return minutes
    return _DEFAULT_VISIT_TIME


def route_minutes(summary: dict | None) -> int:
    """Valhalla trip time (seconds) rounded up to whole minutes."""
    if not summary:
        return 0
    return int((summary.get("time") or 0) / 60) + 1


def embed_query(text: str) -> list[float]:
    # fastembed returns a numpy array; psycopg3 can't auto-adapt that into a
    # pgvector via %s::vector, so materialize to a plain Python list.
    import numpy as np
    arr = list(state["embedder"].embed([text]))[0]
    return arr.tolist() if isinstance(arr, np.ndarray) else list(arr)


def greedy_order(cands: list[dict], n: int) -> list[dict]:
    """Pick n candidates, starting from the highest-ranked, then nearest-neighbour."""
    if not cands:
        return []
    chosen = [cands[0]]
    pool = cands[1:]
    while len(chosen) < n and pool:
        last = chosen[-1]
        pool.sort(key=lambda c: (c["lat"] - last["lat"]) ** 2 + (c["lon"] - last["lon"]) ** 2)
        chosen.append(pool.pop(0))
    return chosen


def to_locations(points: list[dict]) -> list[dict]:
    """Valhalla expects first/last typed 'break', intermediates 'via'."""
    out = []
    for i, p in enumerate(points):
        out.append({"lat": p["lat"], "lon": p["lon"], "type": "break" if i in (0, len(points) - 1) else "via"})
    return out


def sanitize_bbox(bbox) -> list[float] | None:
    """Drop malformed bboxes hallucinated by the small LLM.

    Accepts the object form {"south": "53.6", "west": ..., "north": ..., "east": ...}
    produced by the grammar-constrained LLM, as well as the legacy array form
    [south, west, north, east] (from the regex fallback). Rejects swapped
    lat/lon axes, reversed corners, and values outside the Grodno region.
    """
    if isinstance(bbox, dict):
        try:
            bbox = [float(bbox[k]) for k in ("south", "west", "north", "east")]
        except (KeyError, TypeError, ValueError):
            return None
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    s, w, n, e = bbox
    if not all(isinstance(v, (int, float)) for v in (s, w, n, e)):
        return None
    # Grodno region: lat ~53-54 N, lon ~23-27 E; anything else is a
    # hallucination (usually swapped axes), so ignore it.
    if not (53.0 <= s <= n <= 54.2 and 23.0 <= w <= e <= 27.5):
        return None
    return [s, w, n, e]


@app.post("/routes/generate")
def generate(req: GenerateReq):
    parsed = parse_query(req.query, default_n_points=req.n_points)
    # An explicit n_points from the client wins over whatever the small LLM
    # guessed from the text (it tends to answer with small numbers).
    n = req.n_points if req.n_points != 4 else parsed.get("n_points")
    n = max(2, min(int(n or 4), 10))
    bbox = sanitize_bbox(parsed.get("region_bbox"))
    # Same for the time budget: explicit request field > LLM-extracted value.
    budget = req.time_budget_minutes or parsed.get("time_budget_minutes")
    if budget is not None:
        budget = max(15, min(int(budget), 600))

    qvec = embed_query(req.query)
    # Categories from the query (LLM-extracted) narrow the candidate pool.
    # Filtered spots come first; if there are fewer than requested, top up
    # with the best unfiltered ones instead of dropping the filter entirely.
    db_cats = db_categories(parsed.get("categories") or [])
    rows: list[dict] = []
    if db_cats:
        rows = candidates_by_embedding(state["db"], qvec, limit=50, region_bbox=bbox, categories=db_cats)
    if len(rows) < n:
        filtered_ids = {r["id"] for r in rows}
        extra = candidates_by_embedding(state["db"], qvec, limit=50, region_bbox=bbox)
        rows.extend(r for r in extra if r["id"] not in filtered_ids)
    if len(rows) < n:
        raise HTTPException(404, f"only {len(rows)} candidates within scope; need {n}")

    chosen = greedy_order(rows, n)
    shape, summary = route_through(VALHALLA_URL, to_locations(chosen))

    # Fit into the time budget: walk time + visit time at each stop. Drop the
    # least-relevant stop (last picked by greedy_order) and re-route until it
    # fits or only 2 stops remain.
    budget_info = None
    if budget is not None:
        while len(chosen) > 2:
            total = route_minutes(summary) + sum(visit_time(p["category"]) for p in chosen)
            if total <= budget:
                break
            chosen = chosen[:-1]
            shape, summary = route_through(VALHALLA_URL, to_locations(chosen))
        total = route_minutes(summary) + sum(visit_time(p["category"]) for p in chosen)
        budget_info = {
            "budget_minutes": budget,
            "total_minutes": total,
            "walk_minutes": route_minutes(summary),
            "visit_minutes": total - route_minutes(summary),
            "fits": total <= budget,
        }

    return {
        "parsed": parsed,
        **({"budget": budget_info} if budget_info else {}),
        "points": [{"id": p["id"], "name": p["name"], "category": p["category"], "lat": p["lat"], "lon": p["lon"]} for p in chosen],
        "shape": shape,
        "summary": summary,
    }


@app.post("/routes/reroute")
def reroute(req: RerouteReq):
    rows = fetch_points_by_ids(state["db"], req.point_ids)
    if len(rows) != len(req.point_ids):
        raise HTTPException(404, "one or more point_ids not found")
    shape, summary = route_through(VALHALLA_URL, to_locations(rows))
    return {
        "points": [{"id": p["id"], "name": p["name"], "category": p["category"], "lat": p["lat"], "lon": p["lon"]} for p in rows],
        "shape": shape,
        "summary": summary,
    }

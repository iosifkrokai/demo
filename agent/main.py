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
from .search import candidates_by_embedding, fetch_points_by_ids
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


class RerouteReq(BaseModel):
    point_ids: list[int] = Field(min_length=2)


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


@app.post("/routes/generate")
def generate(req: GenerateReq):
    parsed = parse_query(req.query, default_n_points=req.n_points)
    n = parsed.get("n_points") or req.n_points
    n = max(2, min(n, 10))

    qvec = embed_query(req.query)
    rows = candidates_by_embedding(state["db"], qvec, limit=50, region_bbox=parsed.get("region_bbox"))
    if len(rows) < n:
        raise HTTPException(404, f"only {len(rows)} candidates within scope; need {n}")

    chosen = greedy_order(rows, n)
    shape, summary = route_through(VALHALLA_URL, to_locations(chosen))

    return {
        "parsed": parsed,
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

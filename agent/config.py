"""Centralised configuration. All env reads happen here."""

from __future__ import annotations

import os


class Settings:
    # --- HTTP ---
    HOST: str = os.environ.get("AGENT_HOST", "0.0.0.0")
    PORT: int = int(os.environ.get("AGENT_PORT", "8080"))

    # --- Database ---
    DSN: str = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")

    # --- Valhalla ---
    VALHALLA_URL: str = os.environ.get("VALHALLA_URL", "http://localhost:8002")
    VALHALLA_TIMEOUT_S: float = float(os.environ.get("VALHALLA_TIMEOUT_S", "20.0"))
    VALHALLA_MAX_RETRIES: int = int(os.environ.get("VALHALLA_MAX_RETRIES", "2"))

    # --- Models ---
    EMBED_MODEL: str = os.environ.get(
        "EMBED_MODEL",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    LLM_MODEL: str = os.environ.get("LLM_MODEL", "Qwen/Qwen2.5-1.5B-Instruct-GGUF")
    LLM_FILE: str = os.environ.get("LLM_FILE", "*q4_k_m.gguf")
    LLM_CTX: int = int(os.environ.get("LLM_CTX", "2048"))
    LLM_THREADS: int = int(os.environ.get("LLM_THREADS", "2"))

    # --- LLM reranker (two passes, two models) ---
    # Pass 1 re-scores the retrieval pool (cheap, many tokens) — a fast model is
    # enough and keeps generate() latency sane. Pass 2 critiques the finished
    # route (few tokens, needs real reasoning) — that one gets the big model.
    RERANK_ENABLED: bool = os.environ.get("RERANK_ENABLED", "1") not in ("0", "false", "")
    RERANK_FAST_MODEL: str = os.environ.get("RERANK_FAST_MODEL", "google/gemini-2.5-flash")
    RERANK_PRO_MODEL: str = os.environ.get("RERANK_PRO_MODEL", "google/gemini-3.1-pro")
    RERANK_POOL: int = int(os.environ.get("RERANK_POOL", "30"))
    RERANK_TIMEOUT_S: float = float(os.environ.get("RERANK_TIMEOUT_S", "12.0"))
    # How much the LLM score is trusted vs. the embedding distance. 0.65 keeps
    # retrieval as the anchor: the model may reorder within what the vector
    # search already considers plausible, but cannot resurrect a total outlier.
    RERANK_W_LLM: float = float(os.environ.get("RERANK_W_LLM", "0.65"))
    RERANK_MIN_CONFIDENCE: float = float(os.environ.get("RERANK_MIN_CONFIDENCE", "0.5"))
    # Objective weights used by the accept/reject guard in routing.score_route.
    ROUTE_W_RELEVANCE: float = float(os.environ.get("ROUTE_W_RELEVANCE", "60.0"))
    ROUTE_W_DEDUP: float = float(os.environ.get("ROUTE_W_DEDUP", "25.0"))

    # --- Defaults for the planner (override-able per request) ---
    DEFAULT_BUDGET_MIN: int = int(os.environ.get("DEFAULT_BUDGET_MIN", "120"))  # 2-hour walk
    MIN_BUDGET_MIN: int = 15
    MAX_BUDGET_MIN: int = 480

    # --- Search ---
    CANDIDATE_POOL_SIZE: int = 100  # over-fetch for reranking

    # --- Grodno bbox sanity bound for LLM-extracted regions ---
    GRODNO_BBOX = {"south": 52.85, "west": 23.50, "north": 54.10, "east": 26.55}


settings = Settings()

# Aliases so that both `config.RERANK_POOL` (via `from agent import config`)
# and `config.settings.RERANK_POOL` (via `from agent.config import settings`)
# work identically.
DSN = settings.DSN
VALHALLA_URL = settings.VALHALLA_URL
VALHALLA_TIMEOUT_S = settings.VALHALLA_TIMEOUT_S
VALHALLA_MAX_RETRIES = settings.VALHALLA_MAX_RETRIES
EMBED_MODEL = settings.EMBED_MODEL
LLM_MODEL = settings.LLM_MODEL
LLM_CTX = settings.LLM_CTX
LLM_THREADS = settings.LLM_THREADS
RERANK_ENABLED = settings.RERANK_ENABLED
RERANK_FAST_MODEL = settings.RERANK_FAST_MODEL
RERANK_PRO_MODEL = settings.RERANK_PRO_MODEL
RERANK_POOL = settings.RERANK_POOL
RERANK_TIMEOUT_S = settings.RERANK_TIMEOUT_S
RERANK_W_LLM = settings.RERANK_W_LLM
RERANK_MIN_CONFIDENCE = settings.RERANK_MIN_CONFIDENCE
ROUTE_W_RELEVANCE = settings.ROUTE_W_RELEVANCE
ROUTE_W_DEDUP = settings.ROUTE_W_DEDUP
DEFAULT_BUDGET_MIN = settings.DEFAULT_BUDGET_MIN
MIN_BUDGET_MIN = settings.MIN_BUDGET_MIN
MAX_BUDGET_MIN = settings.MAX_BUDGET_MIN
CANDIDATE_POOL_SIZE = settings.CANDIDATE_POOL_SIZE
GRODNO_BBOX = settings.GRODNO_BBOX
HOST = settings.HOST
PORT = settings.PORT

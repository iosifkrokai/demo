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

    # --- LLM reranker (legacy, used by old agent.py) ---
    RERANK_ENABLED: bool = os.environ.get("RERANK_ENABLED", "1") not in ("0", "false", "")
    RERANK_FAST_MODEL: str = os.environ.get("RERANK_FAST_MODEL", "google/gemini-2.5-flash")
    RERANK_PRO_MODEL: str = os.environ.get("RERANK_PRO_MODEL", "google/gemini-3.1-pro")
    RERANK_POOL: int = int(os.environ.get("RERANK_POOL", "30"))
    RERANK_TIMEOUT_S: float = float(os.environ.get("RERANK_TIMEOUT_S", "12.0"))
    RERANK_W_LLM: float = float(os.environ.get("RERANK_W_LLM", "0.65"))
    RERANK_MIN_CONFIDENCE: float = float(os.environ.get("RERANK_MIN_CONFIDENCE", "0.5"))
    ROUTE_W_RELEVANCE: float = float(os.environ.get("ROUTE_W_RELEVANCE", "60.0"))
    ROUTE_W_DEDUP: float = float(os.environ.get("ROUTE_W_DEDUP", "25.0"))

    # --- Legacy planner defaults (used by old agent.py) ---
    DEFAULT_BUDGET_MIN: int = int(os.environ.get("DEFAULT_BUDGET_MIN", "120"))
    MIN_BUDGET_MIN: int = 15
    MAX_BUDGET_MIN: int = 480

    # --- New planner defaults (used by agent.planner.*) ---
    DEFAULT_TIME_BUDGET_MIN: int = int(os.environ.get("DEFAULT_TIME_BUDGET_MIN", "120"))
    MIN_TIME_BUDGET_MIN: int = 15
    MAX_TIME_BUDGET_MIN: int = 480

    # --- Planner tuning ---
    MMR_LAMBDA: float = float(os.environ.get("MMR_LAMBDA", "0.7"))
    RRF_K: int = int(os.environ.get("RRF_K", "60"))
    NEGATIVE_FILTER_ENABLED: bool = os.environ.get("NEGATIVE_FILTER_ENABLED", "1") not in ("0", "false", "")

    # --- Pool sizes ---
    RETRIEVAL_POOL_SIZE: int = int(os.environ.get("RETRIEVAL_POOL_SIZE", "50"))
    RERANK_POOL_SIZE: int = int(os.environ.get("RERANK_POOL_SIZE", "30"))
    MMR_POOL_SIZE: int = int(os.environ.get("MMR_POOL_SIZE", "12"))
    ROUTE_MAX_STOPS: int = int(os.environ.get("ROUTE_MAX_STOPS", "8"))

    # --- Gemini (DeepInfra OpenAI-compatible) for intent extraction ---
    GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "google/gemini-2.5-flash")
    GEMINI_TIMEOUT_S: float = float(os.environ.get("GEMINI_TIMEOUT_S", "3.0"))
    DEEPINFRA_URL: str = "https://api.deepinfra.com/v1/openai"

    # --- BGE cross-encoder reranker ---
    RERANK_BACKEND: str = os.environ.get("RERANK_BACKEND", "bge")  # "bge" | "off"
    BGE_RERANK_MODEL: str = os.environ.get("BGE_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")

    # --- Search ---
    CANDIDATE_POOL_SIZE: int = 100

    # --- Grodno bbox sanity bound ---
    GRODNO_BBOX = {"south": 52.85, "west": 23.50, "north": 54.10, "east": 26.55}


settings = Settings()

# Module-level aliases (both `config.X` and `config.settings.X` work)
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
DEFAULT_TIME_BUDGET_MIN = settings.DEFAULT_TIME_BUDGET_MIN
MIN_TIME_BUDGET_MIN = settings.MIN_TIME_BUDGET_MIN
MAX_TIME_BUDGET_MIN = settings.MAX_TIME_BUDGET_MIN
MMR_LAMBDA = settings.MMR_LAMBDA
RRF_K = settings.RRF_K
NEGATIVE_FILTER_ENABLED = settings.NEGATIVE_FILTER_ENABLED
RETRIEVAL_POOL_SIZE = settings.RETRIEVAL_POOL_SIZE
RERANK_POOL_SIZE = settings.RERANK_POOL_SIZE
MMR_POOL_SIZE = settings.MMR_POOL_SIZE
ROUTE_MAX_STOPS = settings.ROUTE_MAX_STOPS
GEMINI_MODEL = settings.GEMINI_MODEL
GEMINI_TIMEOUT_S = settings.GEMINI_TIMEOUT_S
DEEPINFRA_URL = settings.DEEPINFRA_URL
RERANK_BACKEND = settings.RERANK_BACKEND
BGE_RERANK_MODEL = settings.BGE_RERANK_MODEL
CANDIDATE_POOL_SIZE = settings.CANDIDATE_POOL_SIZE
GRODNO_BBOX = settings.GRODNO_BBOX
HOST = settings.HOST
PORT = settings.PORT

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

    # --- Defaults for the planner (override-able per request) ---
    DEFAULT_N_POINTS: int = int(os.environ.get("DEFAULT_N_POINTS", "4"))
    MIN_N_POINTS: int = 2
    MAX_N_POINTS: int = 10
    MIN_BUDGET_MIN: int = 15
    MAX_BUDGET_MIN: int = 600

    # --- Search ---
    CANDIDATE_POOL_SIZE: int = 50

    # --- Grodno bbox sanity bound for LLM-extracted regions ---
    GRODNO_BBOX = {"south": 52.85, "west": 23.50, "north": 54.10, "east": 26.55}


settings = Settings()

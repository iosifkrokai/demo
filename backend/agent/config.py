"""Deployment configuration — secrets and addresses ONLY.

Everything else (models, weights, thresholds, limits) lives in
constants.py as reviewable code. Environment variables here are
deployment facts, not tuning knobs:

    OPENROUTER_API_KEY  — secret; Jev + embeddings (required for ML paths)
    DATABASE_URL        — Postgres DSN (default: local dev compose)
    VALHALLA_URL        — routing engine address
    AGENT_HOST/PORT     — bind address
"""

from __future__ import annotations

import os


class Settings:
    # ── Secrets ──
    OPENROUTER_API_KEY: str | None = os.environ.get("OPENROUTER_API_KEY") or None

    # ── Addresses ──
    HOST: str = os.environ.get("AGENT_HOST", "0.0.0.0")
    PORT: int = int(os.environ.get("AGENT_PORT", "8080"))
    DSN: str = os.environ.get(
        "DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno"
    )
    VALHALLA_URL: str = os.environ.get("VALHALLA_URL", "http://localhost:8002")


settings = Settings()

DSN = settings.DSN
VALHALLA_URL = settings.VALHALLA_URL
HOST = settings.HOST
PORT = settings.PORT

"""Deployment configuration — secrets and addresses ONLY.

Everything else (models, weights, thresholds, limits) lives in
constants.py as reviewable code. Environment variables here are
deployment facts, not tuning knobs:

    OPENROUTER_API_KEY  — secret; the interpretation agent + embeddings
                          (required for ML paths)
    DATABASE_URL        — Postgres DSN (default: local dev compose)
    VALHALLA_URL        — routing engine address
    AGENT_HOST/PORT     — bind address
    AGENT_INTERPRET_MODEL — optional override of the agent's model (constants
                          are not env; this exists so a benchmark can pick one)
"""

from __future__ import annotations

import os


class Settings:
    # Secrets
    OPENROUTER_API_KEY: str | None = os.environ.get("OPENROUTER_API_KEY") or None

    # Observability (self-hosted Langfuse)
    # Optional: with no public/secret key the trace exporter is a no-op and the
    # agent answers as before. HOST defaults to the local compose mapping; the
    # agent container overrides it to the compose service name (langfuse-web).
    LANGFUSE_HOST: str | None = os.environ.get("LANGFUSE_HOST")
    LANGFUSE_PUBLIC_KEY: str | None = os.environ.get("LANGFUSE_PUBLIC_KEY")
    LANGFUSE_SECRET_KEY: str | None = os.environ.get("LANGFUSE_SECRET_KEY")

    # Addresses
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


def openrouter_api_key() -> str | None:
    """The OpenRouter key for this process, or None.

    Read from the environment on every call (the live source of truth) with the
    import-time settings snapshot as a fallback.  Every OpenRouter caller goes
    through here — the interpretation agent (planner/agent_interpret.py) and the
    embeddings client (planner/pipeline.py) — so "is OpenRouter configured?" has
    exactly one answer in the process, and /health can report it honestly.
    """
    return os.environ.get("OPENROUTER_API_KEY") or settings.OPENROUTER_API_KEY


def langfuse_configured() -> bool:
    """True when both Langfuse keys are present (a host alone is not enough).

    The trace exporter checks this before touching the SDK: without keys the
    client would still try to POST to Langfuse, and a tracing target that is
    only half-configured must not cost a request anything.
    """
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY") or settings.LANGFUSE_PUBLIC_KEY
    ) and bool(os.environ.get("LANGFUSE_SECRET_KEY") or settings.LANGFUSE_SECRET_KEY)

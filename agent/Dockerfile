# ─────────────────────────────────────────────────────────────────────────────
# Agent container (FastAPI + Grodno planner).
#
# Layer caching strategy:
#   1. Copy only pyproject.toml first (lockfile is optional on first build)
#   2. uv lock + sync (deps layer cached until deps change)
#   3. Copy source code last (invalidated on any .py change)
#
# uv is ~10× faster than pip and handles resolution in one step.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

# System deps: libgomp for onnxruntime (fastembed), curl for healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (pinned for reproducibility — bump if you need newer).
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app

# ── Layer 1: project metadata + deps (cache-friendly) ──
# uv.lock is optional on first build; subsequent builds need it for --frozen.
COPY pyproject.toml uv.lock* ./

# Generate lockfile if missing, then sync deps only (skip project install).
# --no-dev: skip ruff/pyright/pytest at runtime.
# --no-install-project: install deps first; project source comes in layer 2.
RUN uv lock && \
    uv sync --no-install-project --no-dev

# ── Layer 2: source code (changes often, cached separately) ──
COPY . /app/agent/

# Now install the project itself on top of the cached deps.
RUN uv sync --no-dev

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENT_HOST=0.0.0.0 \
    AGENT_PORT=8080 \
    DATABASE_URL=postgresql://grodno:grodno@db:5432/grodno \
    VALHALLA_URL=http://valhalla:8002

EXPOSE 8080

# Healthcheck — docker compose / k8s rely on this.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

# Use the venv uv created (uv sync → .venv by default).
# --no-dev guarantees no ruff/pyright/pytest get loaded at runtime.
CMD ["uv", "run", "--no-dev", "python", "-m", "agent.main"]

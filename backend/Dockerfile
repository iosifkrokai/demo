# Agent container (FastAPI + Grodno planner).
# Build context: backend/  (docker compose builds with context: ./backend)
# All ML models via OpenRouter — no local ML dependencies.

FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv lock && uv sync --no-install-project --no-dev

COPY agent/ /app/agent/
RUN uv sync --no-dev

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENT_HOST=0.0.0.0 \
    AGENT_PORT=8080 \
    DATABASE_URL=postgresql://grodno:grodno@db:5432/grodno \
    VALHALLA_URL=http://valhalla:8002

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["uv", "run", "--no-dev", "python", "-m", "agent.main"]

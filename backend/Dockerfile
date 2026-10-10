FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/
ENV UV_HTTP_TIMEOUT=300

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv lock && uv sync --no-install-project --no-dev

COPY agent/ /app/agent/
COPY api/ /app/api/
COPY contracts/ /app/contracts/
COPY core/ /app/core/
COPY data/ /app/data/
COPY db/ /app/db/
COPY ml/ /app/ml/
COPY planner/ /app/planner/
COPY reference/ /app/reference/
COPY telemetry/ /app/telemetry/
RUN uv sync --no-dev

ENV FASTEMBED_CACHE_PATH=/opt/fastembed \
    HF_HOME=/opt/hf \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENT_HOST=0.0.0.0 \
    AGENT_PORT=8080 \
    DATABASE_URL=postgresql://grodno:grodno@db:5432/grodno \
    VALHALLA_URL=http://valhalla:8002

RUN /app/.venv/bin/python -c \
    "import ml.embeddings as e; e.embed_query('warmup'); print('embedding model baked:', e.MODEL_NAME)"

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["/app/.venv/bin/python", "-m", "api.main"]

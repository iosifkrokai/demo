# Agent + seed container (FastAPI planner; the seed CLI shares this image).
# Build context: backend/  (docker compose builds with context: ./backend)
#
# Embeddings are computed LOCALLY on the CPU (fastembed/ONNX,
# intfloat/multilingual-e5-small, 384-d). The model weights are baked into the
# image at build time, so the container runs offline and reproduces exactly.

FROM python:3.12-slim AS base

# libgomp1 is required by onnxruntime at import time.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv lock && uv sync --no-install-project --no-dev

COPY agent/ /app/agent/
COPY api/ /app/api/
COPY contracts/ /app/contracts/
COPY core/ /app/core/
COPY domain/ /app/domain/
COPY infra/ /app/infra/
COPY store/ /app/store/
# The taxonomy data is needed at `domain.constants` import time: without it the
# container dies on start with FileNotFoundError and the compose service never
# comes up.
COPY data/ /app/data/
# The seed CLI (`python -m seed`) lives in this image too.
COPY seed/ /app/seed/
RUN uv sync --no-dev

# Bake the embedding model into the image. FASTEMBED_CACHE_PATH / HF_HOME pin the
# download location; the warm-up has no `|| true`, so an unreachable model FAILS
# the build instead of producing an image that cannot embed.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed \
    HF_HOME=/opt/hf \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENT_HOST=0.0.0.0 \
    AGENT_PORT=8080 \
    DATABASE_URL=postgresql://grodno:grodno@db:5432/grodno \
    VALHALLA_URL=http://valhalla:8002

RUN /app/.venv/bin/python -c \
    "import agent.embeddings as e; e.embed_query('warmup'); print('embedding model baked:', e.MODEL_NAME)"

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

# Run the venv's python directly: `uv run` re-resolves deps at every start and
# leaves the container in a restart loop when the network is down.
CMD ["/app/.venv/bin/python", "-m", "api.main"]

FROM python:3.12-slim

WORKDIR /app

# Install system deps for fastembed (onnxruntime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY agent/requirements.txt /app/agent/requirements.txt
RUN pip install --no-cache-dir -r /app/agent/requirements.txt

COPY agent/ /app/agent/

# Embedder model is downloaded at startup by fastembed (cached in /root/.cache)
ENV PYTHONUNBUFFERED=1
ENV AGENT_HOST=0.0.0.0
ENV AGENT_PORT=8080
ENV DATABASE_URL=postgresql://grodno:grodno@db:5432/grodno
ENV VALHALLA_URL=http://valhalla:8002
ENV DEEPINFRA_API_KEY=${DEEPINFRA_API_KEY:-}

EXPOSE 8080

CMD ["python", "-m", "agent.main"]

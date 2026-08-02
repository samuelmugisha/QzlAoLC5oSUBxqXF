# Bitcoin trading agent — production image
FROM python:3.11-slim

# System deps: none of the current modules need compiled extensions beyond
# what pip wheels provide, but curl is handy for the healthcheck below.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code.
COPY src/ ./src/
COPY scheduler.py .

# Persistent state (portfolio, trade log, config cache, LLM audit log)
# lives here — mount this as a named volume so a container restart or
# redeploy doesn't lose trading history or the drawdown high-water mark.
VOLUME ["/app/data"]
RUN mkdir -p /app/data /app/config

# Secrets are injected as environment variables at deploy time (Docker
# secret, docker run --env-file, or the platform's secret manager) —
# never baked into the image. See .env.example for the full list.
ENV PYTHONUNBUFFERED=1

# Basic liveness signal: the scheduler process must be running.
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f scheduler.py || exit 1

ENTRYPOINT ["python", "scheduler.py"]

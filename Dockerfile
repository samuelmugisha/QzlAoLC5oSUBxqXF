# Bitcoin trading agent — combined scheduler + dashboard image
#
# Runs both the trading scheduler and the Streamlit dashboard in one
# container (see docker-entrypoint.sh) so they share the same
# filesystem: the dashboard reads the live portfolio/trade state the
# scheduler is writing, not a disconnected copy. This matters on
# DigitalOcean App Platform, where every separate component gets its
# own isolated disk — a Worker + Web Service split can never see the
# same trade history without external shared storage.
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
COPY streamlit_app.py .
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Persistent state (portfolio, trade log, config cache, LLM audit log)
# lives here — mount this as a named volume so a container restart or
# redeploy doesn't lose trading history or the drawdown high-water mark.
VOLUME ["/app/data"]
RUN mkdir -p /app/data /app/config

# Secrets are injected as environment variables at deploy time (Docker
# secret, docker run --env-file, or the platform's secret manager) —
# never baked into the image. See .env.example for the full list.
ENV PYTHONUNBUFFERED=1

# The platform (e.g. DigitalOcean App Platform) injects $PORT at
# runtime; Streamlit binds to it in docker-entrypoint.sh. 8080 is the
# fallback for local `docker run` / docker-compose.
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f "http://localhost:${PORT:-8080}/_stcore/health" || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]

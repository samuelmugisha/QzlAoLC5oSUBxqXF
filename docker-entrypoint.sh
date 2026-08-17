#!/bin/bash
# Runs the scheduler and the Streamlit dashboard as sibling processes in
# one container so they share /app/data — the dashboard reads live
# portfolio/trade state the scheduler is writing, not a disconnected copy.
set -e

python scheduler.py &
SCHEDULER_PID=$!

trap 'kill -TERM "$SCHEDULER_PID" "$STREAMLIT_PID" 2>/dev/null' TERM INT

streamlit run streamlit_app.py \
    --server.port="${PORT:-8080}" \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false &
STREAMLIT_PID=$!

# Exit (and let the platform restart the container) if either process dies.
wait -n "$SCHEDULER_PID" "$STREAMLIT_PID"
EXIT_CODE=$?
kill -TERM "$SCHEDULER_PID" "$STREAMLIT_PID" 2>/dev/null
exit "$EXIT_CODE"

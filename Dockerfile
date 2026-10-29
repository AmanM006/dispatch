# Build runtime image
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application codebase and benchmarks
COPY dispatch/ ./dispatch/
COPY tests/ ./tests/
COPY BENCHMARK_V3_RESULTS.json .

# Default port for digital twin backend (overridden by hosting via PORT env var)
ENV PORT=8080
EXPOSE 8080

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV ENVIRONMENT=production

# Launch FastAPI digital twin server via Uvicorn respecting $PORT
CMD ["sh", "-c", "exec uvicorn dispatch.twin_server:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]

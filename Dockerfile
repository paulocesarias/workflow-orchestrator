# syntax=docker/dockerfile:1

# Build stage
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Copy source code and README (required by pyproject.toml)
COPY README.md ./
COPY src/ ./src/

# Install the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Runtime stage
FROM python:3.12-slim AS runtime

# Create non-root user
RUN groupadd -r orchestrator && useradd -r -g orchestrator orchestrator

WORKDIR /app

# Copy virtual environment and source from builder
COPY --from=builder --chown=orchestrator:orchestrator /app/.venv /app/.venv
COPY --from=builder --chown=orchestrator:orchestrator /app/src /app/src

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER orchestrator

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Expose port
EXPOSE 8000

# Default command - run the API server
CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]

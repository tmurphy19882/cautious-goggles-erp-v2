# syntax=docker/dockerfile:1.7
# Multi-stage Dockerfile for ERP v2.
# Stage 1: builder — install deps into a venv.
# Stage 2: runtime — slim image with the venv copied across.

ARG PYTHON_VERSION=3.12
ARG POETRY_VERSION=1.8

FROM python:${PYTHON_VERSION}-slim AS builder
WORKDIR /app

# System deps for asyncpg / bcrypt wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Install the package + dev deps into a venv at /app/.venv.
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Stage 2: runtime
FROM python:${PYTHON_VERSION}-slim AS runtime
WORKDIR /app

# Create a non-root user.
RUN groupadd --system erp \
    && useradd --system --gid erp --home /app --shell /sbin/nologin erp

# Copy the venv + source.
COPY --from=builder /app/.venv /app/.venv
COPY services/erp/src /app/src
COPY services/erp/migrations /app/migrations
COPY services/erp/scripts /app/scripts
COPY services/erp/pyproject.toml /app/pyproject.toml
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1

USER erp

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD python -c "import httpx,sys; \
sys.exit(0 if httpx.get('http://localhost:8001/health', timeout=2).status_code==200 else 1)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8001"]

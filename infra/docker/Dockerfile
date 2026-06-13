# syntax=docker/dockerfile:1.7
#
# Unified Dockerfile for CodeOracle process variants.
#
# Build args:
#   VARIANT — api | worker | cli | mcp | agent (default: cli)
#
# Each variant module (codeoracle.api, codeoracle.worker, etc.) provides a
# __main__.py per the Letta `__main__.py-per-process` pattern. Variant
# modules ship with their owning plan (api → Plan 11, mcp → Plan 12,
# workers → Plans 5/6/7, cli → Plan 1, agent → Plan 10). Plan 1A only
# requires `codeoracle.cli` to exist for the smoke test; Plan 1 already
# creates `src/codeoracle/cli/__main__.py` (the Typer `app` scaffold) before this plan runs.

ARG PYTHON_VERSION=3.14-slim-bookworm

# --- Stage 1: build dependencies via uv ---
FROM python:${PYTHON_VERSION} AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

RUN pip install --no-cache-dir uv==0.5.5

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --- Stage 2: runtime base ---
FROM python:${PYTHON_VERSION} AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src

WORKDIR /app

# Install curl for the slice-e2e HTTP healthchecks. The slim base
# (python:3.14-slim-bookworm) omits curl; Plan 13's api/mcp/agent services
# probe via `curl -fsS ...` and would otherwise wedge `unhealthy` at
# compose-up. Added by Plan 13 amendment #49 (2026-05-26). The
# --no-install-recommends + apt cache cleanup keeps the layer minimal
# (~1.5 MB).
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Copy uv-managed venv from deps stage
COPY --from=deps /app/.venv /app/.venv

# Copy source + install the project (no deps — already in venv)
COPY src /app/src
COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir -e . --no-deps

# Copy the entrypoint shim
COPY infra/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Drop privileges (CIS Docker Benchmark 4.1)
RUN useradd --create-home --uid 1000 codeoracle && \
    chown -R codeoracle:codeoracle /app
USER codeoracle

# --- Stage 3: final per-variant image ---
FROM runtime-base AS final

ARG VARIANT=cli
ENV CODEORACLE_VARIANT=${VARIANT}

ENTRYPOINT ["/entrypoint.sh"]

#!/usr/bin/env bash
# bootstrap_dev.sh — fresh-clone to working dev env.
#
# Idempotent: safe to re-run. Each step checks whether the work was
# already done and skips if so.
#
# Usage:
#   bash infra/scripts/bootstrap_dev.sh
#
# What it does:
#   1. Verify Docker daemon is reachable (required for compose stack)
#   2. Install uv if missing
#   3. uv sync (creates .venv + installs deps from uv.lock)
#   4. Install pre-commit hooks
#   5. Print next-step guidance

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

log() {
    echo "[bootstrap] $*"
}

# --- Step 1: Docker daemon reachable ---
if ! docker info >/dev/null 2>&1; then
    log "WARNING: Docker daemon not reachable. Compose stack will be unavailable."
    log "         Install Docker Desktop (macOS/Windows) or docker.io (Linux)."
fi

# --- Step 2: Install uv if missing ---
if ! command -v uv >/dev/null 2>&1; then
    log "Installing uv..."
    curl -fsSL https://astral.sh/uv/install.sh | sh
    # Source the new PATH; subsequent commands need uv on PATH
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.local/bin/env"
    fi
    export PATH="$HOME/.local/bin:$PATH"
fi
log "uv version: $(uv --version)"

# --- Step 3: Sync dependencies ---
log "Syncing dependencies (uv sync)..."
uv sync --frozen

# --- Step 4: Install pre-commit hooks ---
if [ -f ".pre-commit-config.yaml" ]; then
    log "Installing pre-commit hooks..."
    uv run pre-commit install
fi

# --- Step 5: Next-step guidance ---
cat <<EOF
[bootstrap] Done.

Next steps:
  make test           # run the full test suite
  make compose-up     # bring up the observability + DB stack
  make help           # see all available targets

EOF

#!/usr/bin/env bash
# seed_test_corpus.sh — populate tests/fixtures/corpus/ from src/codeoracle/.
#
# The slice's corpus is the platform's own source code (dogfood
# property). This script freezes a copy under tests/fixtures/corpus/
# so slice integration tests have a deterministic input that is
# independent of in-flight edits to src/codeoracle/.
#
# Idempotent: removes the corpus dir first, then re-populates.
#
# Usage:
#   bash infra/scripts/seed_test_corpus.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="${REPO_ROOT}/src"
CORPUS="${REPO_ROOT}/tests/fixtures/corpus"

log() {
    echo "[seed_test_corpus] $*"
}

log "Cleaning corpus dir: ${CORPUS}"
rm -rf "${CORPUS}"
mkdir -p "${CORPUS}"

log "Copying ${SRC}/codeoracle → ${CORPUS}/codeoracle"
# rsync excludes __pycache__ and *.pyc — keep corpus deterministic
rsync -a --quiet \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude="*.pyo" \
    --exclude=".cache" \
    --exclude=".mypy_cache" \
    --exclude=".ruff_cache" \
    "${SRC}/codeoracle/" "${CORPUS}/codeoracle/"

log "Done. Corpus contents:"
find "${CORPUS}" -maxdepth 2 -type d | sort

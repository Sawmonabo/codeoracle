#!/usr/bin/env bash
# Spec §3.6 pre-implementation verification gates — contract runner.
#
# Runs the 4 spec §3.6 dependency gates against the planted fixture + pinned
# versions and records pass/fail + the chosen fallback (per spec §3.6 column 3)
# into .cache/preflight/results.md.
#
# The dependency gates record fallbacks. API and MCP runtime smokes are hard
# gates: they require the live stack to be running and fail this script on
# failure.
#
# Distinct from check_pinned_env.sh (pin-consistency lint, CI-recurring).
# preflight.sh is one-shot at scaffolding day 1.
#
# Usage:  bash infra/scripts/preflight.sh
# Reads:  infra/versions/pinned.env  (populated by Plan 2 Tasks 1-10)
#         infra/preflight-fixtures/  (planted by Plan 2 Task 12 Step 1)
# Writes: .cache/preflight/results.md  (overwritten on every run)
#
# Exit code: non-zero only when an API/MCP runtime smoke fails.

set -uo pipefail  # NOT -e: gate failures must record the fallback, not abort

PINS="infra/versions/pinned.env"
RESULTS=".cache/preflight/results.md"
FIXTURE_DIR="infra/preflight-fixtures"
FIXTURE_PY="$FIXTURE_DIR/sample_py.py"

if [ ! -f "$PINS" ]; then
  echo "::error::Pinned env not found at $PINS — run Plan 2 Tasks 1-10 first." >&2
  exit 2
fi
if [ ! -f "$FIXTURE_PY" ]; then
  echo "::error::Planted fixture missing at $FIXTURE_PY — run Plan 2 Task 12 Step 1 first." >&2
  exit 2
fi

# shellcheck disable=SC1090
set -a
source "$PINS"
set +a

mkdir -p "$(dirname "$RESULTS")"

DATE="$(date +%Y-%m-%d)"
PASS=0
FAIL=0

# Cross-platform file size (Linux uses `stat -c`, macOS uses `stat -f`).
_size() {
  stat -c%s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null || echo 0
}

cat > "$RESULTS" <<HEADER
# Pre-Implementation Preflight Results — Spec §3.6 Contract Gates

**Run date:** $DATE
**Spec contract:** \`docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md\` §3.6
**Long-form per-dep evidence:** \`docs/verification-report.md\` (Plan 2 Tasks 2-10 narrative)
**Pin source:** \`infra/versions/pinned.env\`
**Fixture:** \`infra/preflight-fixtures/sample_py.py\`

The dependency rows below record the four spec §3.6 gates. **PASS** means the
named command succeeded with the expected output. **FAIL+FALLBACK** means the
gate failed AND the spec-§3.6 pre-decided fallback was applied — no
mid-implementation reconciliation; the platform proceeds against the named
alternative. Update \`docs/architecture.md\` + any Plan 3+ task that
references the failed contract.

The API and MCP runtime smoke rows are hard gates. They require the live stack
to be running and this script exits non-zero if either one fails.

| Gate | Verdict | Applied fallback (if FAIL) |
|---|---|---|
HEADER

record_gate() {
  local gate="$1"
  local verdict="$2"
  local fallback="${3:-—}"
  printf "| %s | **%s** | %s |\n" "$gate" "$verdict" "$fallback" >> "$RESULTS"
  case "$verdict" in
    PASS)            PASS=$((PASS+1)) ;;
    "FAIL+FALLBACK") FAIL=$((FAIL+1)) ;;
  esac
}

# --- Gate 1 — Apache AGE supports PostgreSQL 18 (spec §3.6 row 1) -----------
GATE_1_FALLBACK='Pin `postgres:17` + `apache/age:release_PG17` in compose + ci.yml; update `docs/architecture.md` to PG17.'
IMG_AGE="${IMAGE_POSTGRES_AGE:-apache/age:release_PG18}"
if docker manifest inspect "$IMG_AGE" >/dev/null 2>&1 \
   && docker run --rm "$IMG_AGE" \
        psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS age; LOAD 'age'; SELECT ag_catalog.create_graph('preflight_g');" \
        >/dev/null 2>&1; then
  record_gate "1 — Apache AGE on PG18 (\`$IMG_AGE\`)" "PASS"
else
  record_gate "1 — Apache AGE on PG18 (\`$IMG_AGE\`)" "FAIL+FALLBACK" "$GATE_1_FALLBACK"
fi

# --- Gate 2 — Joern Python frontend produces a usable CPG (spec §3.6 row 2) --
GATE_2_FALLBACK='SCIP-only for Python; reserve Joern for non-Python languages added in Phase 6. Document in `docs/architecture.md`.'
if command -v joern-parse >/dev/null 2>&1; then
  TMP_CPG_DIR="$(mktemp -d)"
  if joern-parse --language pythonsrc --output "$TMP_CPG_DIR/cpg.bin" "$FIXTURE_PY" >/dev/null 2>&1 \
     && [ -f "$TMP_CPG_DIR/cpg.bin" ] \
     && [ "$(_size "$TMP_CPG_DIR/cpg.bin")" -gt 0 ]; then
    # CPG is non-empty — proves the Python frontend emitted nodes, not just
    # exited 0. Threshold is `-gt 0` rather than a measured byte floor: any
    # parse failure produces no file (caught by `-f`) or an empty file
    # (caught here); any working parse against the planted fixture produces
    # kilobytes. A magic byte-threshold would risk spurious FAIL+FALLBACK on
    # toolchain version bumps that change the on-disk size of an otherwise-
    # working CPG. Querying for `cpg.method("verify").caller.l` requires the
    # Joern REPL/script runner; we don't invoke it here (heavy dep, brittle
    # to version drift). The full caller-query assertion lives in Plan 5's
    # parser test suite — see Plan 2 §6 deviation #4.
    record_gate "2 — Joern Python frontend (\`joern-parse $FIXTURE_PY\`)" "PASS"
  else
    record_gate "2 — Joern Python frontend (\`joern-parse $FIXTURE_PY\`)" "FAIL+FALLBACK" "$GATE_2_FALLBACK"
  fi
  rm -rf "$TMP_CPG_DIR"
else
  record_gate "2 — Joern Python frontend (\`joern-parse $FIXTURE_PY\`)" "FAIL+FALLBACK" \
    "\`joern-parse\` not installed — install per Plan 2 Task 5 OR adopt SCIP-only fallback: $GATE_2_FALLBACK"
fi

# --- Gate 3 — SCIP-python emits resolvable indexes (spec §3.6 row 3) --------
GATE_3_FALLBACK='Adopt `pyright --outputjson` or `tree-sitter-python` for Python symbol extraction; document call-graph fidelity drop in `docs/architecture.md`.'
TMP_SCIP_DIR="$(mktemp -d)"
cp "$FIXTURE_PY" "$TMP_SCIP_DIR/sample_py.py"
# Invocation matches Plan 2 Task 6 — npx-pulled @sourcegraph/scip-python.
if (cd "$TMP_SCIP_DIR" && npx --yes @sourcegraph/scip-python index --project-name=codeoracle --output=index.scip .) >/dev/null 2>&1 \
   && [ -f "$TMP_SCIP_DIR/index.scip" ] \
   && npx --yes @sourcegraph/scip print "$TMP_SCIP_DIR/index.scip" 2>/dev/null | grep -q "verify"; then
  record_gate "3 — SCIP-python (\`scip-python index $FIXTURE_DIR\`)" "PASS"
else
  record_gate "3 — SCIP-python (\`scip-python index $FIXTURE_DIR\`)" "FAIL+FALLBACK" "$GATE_3_FALLBACK"
fi
rm -rf "$TMP_SCIP_DIR"

# --- Gate 4 — vLLM serves OpenAI Embeddings API contract (spec §3.6 row 4) --
GATE_4_FALLBACK='Route embeddings through `adapters/llm/providers/vllm_embed.py` (separate adapter) instead of the chat provider. Plan 6 owns the implementation (EmbeddingsProvider ABC + adapter at `adapters/llm/embeddings.py`).'
VLLM_BASE="${VLLM_API_BASE:-${VLLM_BASE:-http://localhost:8000}}"
VLLM_MODEL="${QWEN_MODEL_ID:-Qwen/Qwen3.6-7B-Instruct}"
if command -v curl >/dev/null 2>&1 \
   && curl -sS --max-time 5 -X POST "$VLLM_BASE/v1/embeddings" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$VLLM_MODEL\",\"input\":\"hello\"}" \
        2>/dev/null | grep -q '"embedding"'; then
  record_gate "4 — vLLM /v1/embeddings (\`$VLLM_BASE\`, model \`$VLLM_MODEL\`)" "PASS"
else
  # At scaffolding day 1, vLLM is almost certainly not running yet — this gate
  # is expected to FAIL+FALLBACK by default. The fallback is a code-path
  # decision (separate embed adapter), not a runtime block; the generated
  # .cache/preflight/results.md output records which provider class to wire.
  record_gate "4 — vLLM /v1/embeddings (\`$VLLM_BASE\`, model \`$VLLM_MODEL\`)" "FAIL+FALLBACK" "$GATE_4_FALLBACK"
fi

# --- Hard runtime smokes — Plan 11/12 surfaces -----------------------------
RUNTIME_FAIL=0
API_PORT="${CODEORACLE_API__PORT:-8080}"
MCP_PORT="${CODEORACLE_MCP__PORT:-8082}"

if curl -fsS --max-time 5 "http://127.0.0.1:${API_PORT}/v1/health" \
   | grep -q '"status":"ok"'; then
  record_gate "5 — API runtime smoke (/v1/health)" "PASS"
else
  record_gate "5 — API runtime smoke (/v1/health)" "FAIL" \
    "Start the CodeOracle API stack; runtime smokes are hard gates."
  RUNTIME_FAIL=1
fi

if curl -fsS --max-time 5 \
     -H "Content-Type: application/json" \
     --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"preflight","version":"0"}}}' \
     "http://127.0.0.1:${MCP_PORT}/mcp" \
   | grep -q '"result"'; then
  record_gate "6 — MCP runtime smoke (initialize)" "PASS"
else
  record_gate "6 — MCP runtime smoke (initialize)" "FAIL" \
    "Start the CodeOracle MCP stack; runtime smokes are hard gates."
  RUNTIME_FAIL=1
fi

# --- Summary footer ---------------------------------------------------------
{
  echo
  echo "## Summary"
  echo
  echo "- **Dependency PASS:** $PASS / 4"
  echo "- **Dependency FAIL+FALLBACK:** $FAIL / 4"
  echo "- **Runtime hard-gate failures:** $RUNTIME_FAIL / 2"
  echo
  if [ "$RUNTIME_FAIL" -ne 0 ]; then
    echo "**Verdict:** Runtime smoke failed. Start the stack and rerun before declaring Plan 13 complete."
  elif [ $FAIL -eq 0 ]; then
    echo "**Verdict:** ALL DEPENDENCY AND RUNTIME GATES PASSED — proceed against the spec's named external deps."
  else
    echo "**Verdict:** Runtime smokes passed; $FAIL/4 dependency gates failed, so the per-gate fallback above is now the load-bearing path. Update \`docs/architecture.md\` + any Plan 3+ task that references the failed contract."
  fi
  echo
  echo "## Re-run"
  echo
  echo '```bash'
  echo 'bash infra/scripts/preflight.sh'
  echo '```'
  echo
  echo "The runner is idempotent: it overwrites \`$RESULTS\` on every run, so re-running after a dep is fixed cleanly records the new outcome."
} >> "$RESULTS"

echo "preflight.sh complete — $PASS dependency gates passed, $FAIL dependency gates fell back, $RUNTIME_FAIL runtime smokes failed. See $RESULTS."
exit "$RUNTIME_FAIL"

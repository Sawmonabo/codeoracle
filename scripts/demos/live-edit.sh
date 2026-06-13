#!/usr/bin/env bash
set -euo pipefail

API_URL="${CODEORACLE_API_URL:-http://127.0.0.1:${CODEORACLE_API__PORT:-8080}}"
SNAPSHOT_SHA="${1:-slice-live-edit}"

echo "query: live edit reindex"
response="$(curl -fsS "${API_URL}/v1/reindex" \
  -H "content-type: application/json" \
  --data "$(jq -nc --arg sha "$SNAPSHOT_SHA" '{base_sha:$sha}')")"
echo "$response" | jq -r '"trace_id: \(.trace_id // "not-emitted-by-reindex")"'
echo "$response" | jq -r '"status: \(.status // "missing")"'
echo "$response" | jq -r '"verdict.status: \(.status | ascii_upcase)"'
echo "$response" | jq -r '[.extractor_results | keys[]?] | "citation handle types: \(join(","))"'
test "$(echo "$response" | jq -r '.status | ascii_upcase')" = "APPLIED"

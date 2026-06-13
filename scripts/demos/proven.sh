#!/usr/bin/env bash
set -euo pipefail

API_URL="${CODEORACLE_API_URL:-http://127.0.0.1:${CODEORACLE_API__PORT:-8080}}"
QUERY="${1:-Explain the existing slice verifier symbol}"

echo "query: ${QUERY}"
response="$(curl -fsS "${API_URL}/v1/query" \
  -H "content-type: application/json" \
  --data "$(jq -nc --arg query "$QUERY" '{symbol:$query}')")"
echo "$response" | jq -r '"trace_id: \(.trace_id // "missing")"'
echo "$response" | jq -r '"status: \(.status // "missing")"'
echo "$response" | jq -r '"verdict.status: \(.verdict.status // "missing")"'
echo "$response" | jq -r '[.citations[]?.handle.type] | "citation handle types: \(join(","))"'
test "$(echo "$response" | jq -r '.verdict.status')" = "PROVEN"
